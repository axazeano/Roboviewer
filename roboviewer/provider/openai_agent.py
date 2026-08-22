"""The agent loop over an OpenAI-compatible chat-completions API.

Our own tool-calling loop rather than someone else's CLI: base_url points at any
gateway, schemas and retries stay under our control, and the reviewer only needs
four read-only tools.

The agent is told how many turns it has and is asked to wrap up before they run
out. Without that it has no stopping criterion and simply expands to fill the
budget: measured on a 64-file MR, raising max_turns from 15 to 25 left the same
seven of eight agents cut off, at 67% more tokens — and their own summaries read
as finished conclusions. They were not short of turns, they never stopped.

What the loop says to the agent about its turns — the budget note, the wrap-up
warning, the nudge — arrives on the request as `TurnNotes`; the texts are the
review's. Tolerance for custom-provider quirks:
  * the terminal tool may never get called — on the last turn we force tool_choice;
  * the model may return JSON as plain text — we try to parse it out of content;
  * parallel_tool_calls can be switched off for gateways that do not support it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import APIError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from ..config import ProviderConfig, RunConfig
from ..models import Usage
from ..repo.tools import dispatch, parse_arguments
from . import ratelimit
from .ratelimit import RateLimiter
from .request import request_body, request_headers, terminal_tool_choice
from .runner import AgentOutcome, AgentRequest, Runner
from .usage import extract_usage

# (kind, detail) — what the loop says about its own progress, handed to the
# agent's observer: tool calls, retries, pacing, the wrap-up
Progress = Callable[[str, str], None]

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# First retry waits this long, then it doubles. A 429 that names its own delay
# overrides it — the provider knows when its minute rolls over and we do not.
RETRY_DELAY_S = 2.0

# "Service overloaded": the provider shedding load rather than metering it, and
# the answer is the same — come back later, all of us.
SERVICE_OVERLOADED = 503

# How many turns before the limit the agent is asked to land. Two leaves one turn
# to finish the check it is on and one to submit; the forced tool_choice on the
# very last turn stays as the backstop for when it ignores all of this.
WRAP_UP_MARGIN = 2

class OpenAIAgentRunner(Runner):
    name = "openai"

    def __init__(self, provider: ProviderConfig, run_cfg: RunConfig, repo_root: Path,
                 base_ref: str, head_ref: str) -> None:
        self._provider = provider
        self._run_cfg = run_cfg
        self._root = repo_root
        self._base_ref = base_ref
        self._head_ref = head_ref
        self._client = AsyncOpenAI(
            api_key=provider.resolve_api_key(),
            base_url=provider.base_url,
            timeout=provider.timeout_s,
            max_retries=0,  # we retry ourselves so attempts can be logged
        )
        # Auth headers go per-request: that is the only way to drop the Bearer header
        self._headers = request_headers(provider)
        # One budget for the whole run. Every agent reserves from it, because
        # the provider counts the run as a whole and so must we.
        self._limiter = RateLimiter(provider.rate_limits)

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ public

    async def run(self, request: AgentRequest) -> AgentOutcome:
        """The agent, from its prompt to its outcome — and an account of both.

        The loop is wrapped rather than sprinkled with reporting calls because
        it returns from five places, and an outcome that escaped without being
        reported would be missing from an observer's account for the most
        interesting reason there is.
        """
        started = time.monotonic()
        outcome = await self._loop(request)
        request.observer.finished(
            payload=outcome.payload,
            usage=outcome.usage,
            turns=outcome.turns,
            duration_s=time.monotonic() - started,
            error=outcome.error,
            truncated=outcome.truncated,
        )
        return outcome

    # ----------------------------------------------------------------- private

    async def _loop(self, request: AgentRequest) -> AgentOutcome:
        emit = request.observer.progress
        transcript = _Transcript.open(request)
        # The system prompt as the agent received it, budget note included
        request.observer.started(
            system=str(transcript.messages[0]["content"]),
            prompt=request.prompt,
            max_turns=request.settings.max_turns,
        )
        usage = Usage()
        turns = 0

        for turn in range(1, request.settings.max_turns + 1):
            turns = turn
            last_turn = turn == request.settings.max_turns
            try:
                # On the last turn leave no choice: submit the result
                completion = await self._complete(
                    request, transcript, force_terminal=last_turn, emit=emit
                )
            except Exception as exc:  # noqa: BLE001 — surfaced upward as the item status
                return AgentOutcome(payload=None, usage=usage, turns=turns, error=_describe(exc))

            turn_usage = extract_usage(completion)
            usage = usage + turn_usage
            message = completion.choices[0].message
            tool_calls = list(message.tool_calls or [])
            request.observer.replied(turn, message.content, turn_usage, _reasoning(message))

            if not tool_calls:
                outcome = _reply_without_tools(
                    message.content, request, transcript, turn, usage, emit
                )
                if outcome is not None:
                    return outcome
                continue

            transcript.add_reply(message.content, tool_calls)
            submitted = await self._answer_calls(tool_calls, request, transcript, turn, emit)
            if submitted is not None:
                return AgentOutcome(
                    payload=submitted, usage=usage, turns=turns, truncated=last_turn
                )

            _wrap_up(request, transcript, turn, emit)

        return AgentOutcome(
            payload=None, usage=usage, turns=turns, error="turn limit exhausted"
        )

    async def _answer_calls(
        self,
        tool_calls: list[Any],
        request: AgentRequest,
        transcript: _Transcript,
        turn: int,
        emit: Progress,
    ) -> dict[str, Any] | None:
        """Runs the tools the model asked for, and returns the submitted payload
        as soon as one of the calls is the terminal one — the review is over at
        that point, and any call after it in the same reply is moot."""
        last_turn = turn == request.settings.max_turns
        for call in tool_calls:
            fn_name = call.function.name
            args = parse_arguments(call.function.arguments or "")

            if fn_name == request.terminal_name:
                # On the last turn tool_choice left the model no other move,
                # so this submission is the limit talking, not the agent.
                forced = " (forced by the turn limit)" if last_turn else ""
                emit("submit", f"turn {turn}{forced}")
                return args

            emit("tool", f"{fn_name}({_short_args(args)})")
            asked = time.monotonic()
            output = await asyncio.to_thread(
                dispatch,
                self._root,
                fn_name,
                args,
                base_ref=self._base_ref,
                head_ref=self._head_ref,
                max_read_lines=self._run_cfg.max_read_lines,
            )
            request.observer.called(turn, fn_name, args, output, time.monotonic() - asked)
            transcript.add_tool_result(call.id, output)
        return None

    async def _complete(
        self,
        request: AgentRequest,
        transcript: _Transcript,
        *,
        force_terminal: bool,
        emit: Progress,
    ) -> Any:
        return await self._send(self._body(request, transcript, force_terminal), emit)

    def _body(
        self, request: AgentRequest, transcript: _Transcript, force_terminal: bool
    ) -> dict[str, Any]:
        """One request, in the shape this provider was configured to want."""
        kwargs: dict[str, Any] = {
            "model": request.settings.model,
            "messages": transcript.messages,
            "tools": [*request.tools, request.terminal_tool],
            "temperature": request.settings.temperature,
            "max_tokens": request.settings.max_tokens,
            "extra_headers": self._headers,
        }
        if body := request_body(request.settings):
            kwargs["extra_body"] = body
        if force_terminal:
            kwargs["tool_choice"] = terminal_tool_choice(self._provider, request.terminal_name)
        else:
            kwargs["tool_choice"] = "auto"
        if not self._provider.parallel_tool_calls:
            kwargs["parallel_tool_calls"] = False
        return kwargs

    async def _send(self, kwargs: dict[str, Any], emit: Progress) -> Any:
        """Waits for the run's share of the provider, then retries what is worth
        retrying with the delay doubling each time."""
        delay = RETRY_DELAY_S
        last_exc: Exception | None = None
        for attempt in range(1, self._provider.max_retries + 1):
            try:
                return await self._attempt(kwargs, emit)
            except (RateLimitError, APITimeoutError) as exc:
                last_exc = exc
                # Everyone waits, not only whoever was refused: the other agents
                # are a moment from the same answer, and asking again together
                # is what turns a busy minute into a failed run.
                held = self._limiter.pause(ratelimit.retry_after(exc) or delay)
                emit(
                    "retry",
                    f"attempt {attempt}/{self._provider.max_retries}: "
                    f"{type(exc).__name__}, holding every agent {held:.0f}s",
                )
            except APIError as exc:
                status = getattr(exc, "status_code", None)
                # 4xx other than 429 is our own fault; retrying will not help
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                if status == SERVICE_OVERLOADED:
                    # "Come back later" in a different number
                    self._limiter.pause(ratelimit.retry_after(exc) or delay)
                last_exc = exc
                emit("retry", f"attempt {attempt}/{self._provider.max_retries}: HTTP {status}")

            if attempt < self._provider.max_retries:
                await asyncio.sleep(delay)
                delay *= 2

        assert last_exc is not None
        raise last_exc

    async def _attempt(self, kwargs: dict[str, Any], emit: Progress) -> Any:
        """One request, paced against what the run has already spent.

        The raw response rather than the parsed one, because the headers carry
        the provider's current ceilings — and those are worth more than any
        number configured here, since they move with usage.
        """
        reservation = await self._limiter.reserve(
            prompt=ratelimit.estimate_tokens(kwargs["messages"]) + self._tools_estimate(kwargs),
            generated=int(kwargs.get("max_tokens") or 0),
        )
        if reservation.held:
            # Seconds to a whole number reads as "waited 0s" for anything under
            # half of one, which says the opposite of what happened.
            held = reservation.waited
            emit("paced", f"waited {held:.0f}s on {reservation.reason}" if held >= 1
                 else f"waited {held:.1f}s on {reservation.reason}")

        raw = await self._client.chat.completions.with_raw_response.create(**kwargs)
        completion = await _parsed(raw)

        if adopted := self._limiter.observe(raw.headers):
            emit("limits", ", ".join(f"{name} {value}/min" for name, value in adopted.items()))
        usage = extract_usage(completion)
        self._limiter.settle(
            reservation,
            prompt=usage.prompt_tokens,
            uncached=max(0, usage.prompt_tokens - usage.cached_tokens),
            generated=usage.completion_tokens,
        )
        return completion

    def _tools_estimate(self, kwargs: dict[str, Any]) -> int:
        """The schemas go out with every turn and are a real part of the prompt."""
        return ratelimit.estimate_tokens(kwargs.get("tools") or [])


@dataclass
class _Transcript:
    """The conversation as the API wants to see it.

    Assembling these dicts by hand at five points of a turn was most of what
    made the loop hard to read; the shapes live here and the loop is left with
    what it decides.
    """

    messages: list[dict[str, Any]]

    @classmethod
    def open(cls, request: AgentRequest) -> _Transcript:
        budget = request.notes.budget.format(
            max_turns=request.settings.max_turns, terminal=request.terminal_name
        )
        return cls(
            [
                {"role": "system", "content": request.system + budget},
                {"role": "user", "content": request.prompt},
            ]
        )

    def add_reply(self, content: str | None, tool_calls: Sequence[Any] = ()) -> None:
        if not tool_calls:
            self.messages.append({"role": "assistant", "content": content or ""})
            return
        self.messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

    def add_tool_result(self, call_id: str, output: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": call_id, "content": output})

    def say(self, text: str) -> None:
        """A user-role note between turns: the nudge and the wrap-up warning."""
        self.messages.append({"role": "user", "content": text})


def _reply_without_tools(
    content: str | None,
    request: AgentRequest,
    transcript: _Transcript,
    turn: int,
    usage: Usage,
    emit: Progress,
) -> AgentOutcome | None:
    """A reply that called no tools. Returns the outcome when this ends the run,
    or None to go round again after a nudge."""
    last_turn = turn == request.settings.max_turns

    payload = _payload_from_text(content)
    if payload is not None:
        emit("fallback", "payload extracted from the response text")
        return AgentOutcome(payload=payload, usage=usage, turns=turn, truncated=last_turn)

    if last_turn:
        return AgentOutcome(
            payload=None,
            usage=usage,
            turns=turn,
            error=(
                f"the model never called {request.terminal_name} "
                f"in {request.settings.max_turns} turns"
            ),
        )

    transcript.add_reply(content)
    transcript.say(
        request.notes.nudge.format(
            left=request.settings.max_turns - turn, terminal=request.terminal_name
        )
    )
    return None


def _wrap_up(request: AgentRequest, transcript: _Transcript, turn: int, emit: Progress) -> None:
    """Said after the tool results, so it is the last thing the agent reads
    before deciding what to do with the turn it has left."""
    left = request.settings.max_turns - turn
    if 0 < left <= WRAP_UP_MARGIN:
        emit("wrap-up", f"{left} turn(s) left")
        transcript.say(request.notes.wrap_up.format(left=left, terminal=request.terminal_name))


async def _parsed(raw: Any) -> Any:
    """The completion out of a raw response, whichever wrapper the SDK returned.

    `with_raw_response` hands back the legacy wrapper, whose `parse()` is an
    ordinary method — while the newer `AsyncAPIResponse.parse` is a coroutine.
    Awaiting the wrong one raises `'ChatCompletion' object can't be awaited` on
    the very first turn, which is exactly how this was found: on a live gateway,
    not in a test whose stub had been written from the same wrong assumption.
    """
    parsed = raw.parse()
    return await parsed if inspect.isawaitable(parsed) else parsed


def _describe(exc: Exception) -> str:
    """A provider error phrased so it is clear what to do next."""
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        detail = str(getattr(exc, "message", "") or exc)[:300]
        if status in (401, 403):
            return (
                f"HTTP {status}: the provider rejected the key ({detail}). "
                "Dig into it with `roboviewer --check-provider`"
            )
        if status == 404:
            return f"HTTP 404: endpoint or model not found ({detail}). Check base_url and model"
        return f"HTTP {status}: {detail}"
    return f"{type(exc).__name__}: {exc}"


def _reasoning(message: Any) -> str:
    """What the model thought, for the providers that hand it back separately.

    A reasoning model routinely answers with tool calls and an empty `content`,
    putting everything it actually worked out in a field of its own —
    `reasoning_content` for Qwen and DeepSeek, `reasoning` elsewhere. Nothing in
    the review reads it: the loop only needs the tool calls. An observer does,
    because without it the account of a thinking model is a list of greps with
    no reason attached to any of them.
    """
    for name in ("reasoning_content", "reasoning"):
        value = getattr(message, name, None)
        if value is None:
            extra = getattr(message, "model_extra", None)
            value = extra.get(name) if isinstance(extra, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _payload_from_text(content: str | None) -> dict[str, Any] | None:
    """Some gateways return JSON as text instead of a tool_call."""
    if not content:
        return None
    match = _JSON_BLOCK.search(content)
    candidates = [match.group(1)] if match else []
    stripped = content.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("findings" in value or "verdicts" in value):
            return value
    return None


def _short_args(args: dict[str, Any]) -> str:
    parts = []
    for key, value in list(args.items())[:2]:
        text = str(value)
        parts.append(f"{key}={text[:48]}" + ("…" if len(text) > 48 else ""))
    return ", ".join(parts)
