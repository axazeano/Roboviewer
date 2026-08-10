"""Runner on top of an OpenAI-compatible API.

Our own tool-calling loop rather than someone else's CLI: base_url points at any
gateway, schemas and retries stay under our control, and the reviewer only needs
four read-only tools.

The agent is told how many turns it has and is asked to wrap up before they run
out. Without that it has no stopping criterion and simply expands to fill the
budget: measured on a 64-file MR, raising max_turns from 15 to 25 left the same
seven of eight agents cut off, at 67% more tokens — and their own summaries read
as finished conclusions. They were not short of turns, they never stopped.

Tolerance for custom-provider quirks:
  * the terminal tool may never get called — on the last turn we force tool_choice;
  * the model may return JSON as plain text — we try to parse it out of content;
  * parallel_tool_calls can be switched off for gateways that do not support it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import APIError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from .. import ratelimit
from ..config import ProviderConfig, RunConfig
from ..models import Usage
from ..ratelimit import Demand, RateLimiter, Spent
from ..tools import dispatch, parse_arguments
from .base import AgentOutcome, AgentRequest, ProgressHook, Runner

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

# Appended to the system prompt. Protocol rather than review wording, which is
# why it lives with the loop that enforces it and not in prompts/.
BUDGET_NOTE = """

# Turn budget

You have {max_turns} turns. A turn is one reply from you, with or without tool
calls. Plan for that: investigate what matters most first, and call `{terminal}`
while turns remain. A review that is never submitted is a review nobody reads.
"""

WRAP_UP_NOTE = (
    "{left} turn(s) left. Do not start anything new — finish the check you are "
    "on and call `{terminal}` now with what you already have."
)


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
        self._headers = provider.request_headers()
        # One budget for the whole run. Every agent reserves from it, because
        # the provider counts the run as a whole and so must we.
        meter, _ = provider.meter()
        self._limiter = RateLimiter(
            meter,
            provider.rate_limits.per_minute,
            adopt_advertised=provider.rate_limits.adopt_advertised,
        )

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ public

    async def run(
        self, request: AgentRequest, on_progress: ProgressHook | None = None
    ) -> AgentOutcome:
        def emit(kind: str, detail: str) -> None:
            if on_progress:
                on_progress(kind, detail)

        transcript = _Transcript.open(request)
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

            usage = usage + _extract_usage(completion)
            message = completion.choices[0].message
            tool_calls = list(message.tool_calls or [])

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

    # ----------------------------------------------------------------- private

    async def _answer_calls(
        self,
        tool_calls: list[Any],
        request: AgentRequest,
        transcript: _Transcript,
        turn: int,
        emit: ProgressHook,
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
            output = await asyncio.to_thread(
                dispatch,
                self._root,
                fn_name,
                args,
                base_ref=self._base_ref,
                head_ref=self._head_ref,
                max_read_lines=self._run_cfg.max_read_lines,
            )
            transcript.add_tool_result(call.id, output)
        return None

    async def _complete(
        self,
        request: AgentRequest,
        transcript: _Transcript,
        *,
        force_terminal: bool,
        emit: ProgressHook,
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
        if body := request.settings.request_body():
            kwargs["extra_body"] = body
        if force_terminal:
            kwargs["tool_choice"] = self._provider.terminal_tool_choice_value(request.terminal_name)
        else:
            kwargs["tool_choice"] = "auto"
        if not self._provider.parallel_tool_calls:
            kwargs["parallel_tool_calls"] = False
        return kwargs

    async def _send(self, kwargs: dict[str, Any], emit: ProgressHook) -> Any:
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

    async def _attempt(self, kwargs: dict[str, Any], emit: ProgressHook) -> Any:
        """One request, paced against what the run has already spent.

        The raw response rather than the parsed one, because the headers carry
        the provider's current ceilings — and those are worth more than any
        number configured here, since they move with usage.
        """
        reservation = await self._limiter.reserve(
            Demand(
                prompt=ratelimit.estimate_tokens(kwargs["messages"]) + self._tools_estimate(kwargs),
                # The ceiling the request carries, which is the only number that
                # exists before the fact. Whether it is charged at all is the
                # gateway's business, so the meter decides what to do with it.
                output_ceiling=int(kwargs.get("max_tokens") or 0),
            )
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
        usage = _extract_usage(completion)
        self._limiter.settle(
            reservation,
            Spent(
                prompt=usage.prompt_tokens,
                uncached=max(0, usage.prompt_tokens - usage.cached_tokens),
                generated=usage.completion_tokens,
            ),
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
        budget = BUDGET_NOTE.format(
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
    emit: ProgressHook,
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
        f"Keep going. {request.settings.max_turns - turn} turn(s) left, and "
        f"you must call the {request.terminal_name} tool before they run out."
    )
    return None


def _wrap_up(request: AgentRequest, transcript: _Transcript, turn: int, emit: ProgressHook) -> None:
    """Said after the tool results, so it is the last thing the agent reads
    before deciding what to do with the turn it has left."""
    left = request.settings.max_turns - turn
    if 0 < left <= WRAP_UP_MARGIN:
        emit("wrap-up", f"{left} turn(s) left")
        transcript.say(WRAP_UP_NOTE.format(left=left, terminal=request.terminal_name))


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


def _field(obj: Any, name: str, default: int = 0) -> int:
    """Gateways return usage either as objects or as plain dicts."""
    if obj is None:
        return default
    value = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _present(obj: Any, name: str) -> bool:
    """Whether the field is there at all, as opposed to being there and zero."""
    if obj is None:
        return False
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return value is not None


def _cached_tokens(raw: Any) -> tuple[int, bool]:
    """(prefix-cache hits, whether the provider reported them at all).

    A gateway that leaves prompt_tokens_details empty still caches prefixes —
    the absence of the field means the count is unknown, not that it is zero.
    The two are returned apart rather than folded together, because a zero is a
    reason to go looking for an unstable prefix and silence is not.
    """
    details = raw.get("prompt_tokens_details") if isinstance(raw, dict) else getattr(
        raw, "prompt_tokens_details", None
    )
    if _present(details, "cached_tokens"):
        return _field(details, "cached_tokens"), True
    # Anthropic-style shims and DeepSeek use their own field names
    for alias in ("cache_read_input_tokens", "prompt_cache_hit_tokens"):
        if _present(raw, alias):
            return _field(raw, alias), True
    return 0, False


def _extract_usage(completion: Any) -> Usage:
    raw = getattr(completion, "usage", None)
    if raw is None:
        return Usage()
    cached, reported = _cached_tokens(raw)
    return Usage(
        prompt_tokens=_field(raw, "prompt_tokens"),
        completion_tokens=_field(raw, "completion_tokens"),
        cached_tokens=cached,
        cache_reported=reported,
    )


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
