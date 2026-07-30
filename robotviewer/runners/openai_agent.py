"""Runner on top of an OpenAI-compatible API.

Our own tool-calling loop rather than someone else's CLI: base_url points at any
gateway, schemas and retries stay under our control, and the reviewer only needs
four read-only tools.

Tolerance for custom-provider quirks:
  * the terminal tool may never get called — on the last turn we force tool_choice;
  * the model may return JSON as plain text — we try to parse it out of content;
  * parallel_tool_calls can be switched off for gateways that do not support it.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from openai import APIError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from ..config import ProviderConfig, RunConfig
from ..models import Usage
from ..tools import dispatch, parse_arguments
from .base import AgentOutcome, AgentRequest, ProgressHook, Runner

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


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
            max_retries=0,  # we retry ourselves so attempts can be logged in the TUI
        )
        # Auth headers go per-request: that is the only way to drop the Bearer header
        self._headers = provider.request_headers()

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ public

    async def run(self, request: AgentRequest, on_progress: ProgressHook | None = None) -> AgentOutcome:
        def emit(kind: str, detail: str) -> None:
            if on_progress:
                on_progress(kind, detail)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.prompt},
        ]
        all_tools = [*request.tools, request.terminal_tool]
        usage = Usage()
        turns = 0

        for turn in range(1, request.max_turns + 1):
            turns = turn
            last_turn = turn == request.max_turns
            try:
                completion = await self._complete(
                    model=request.model,
                    messages=messages,
                    tools=all_tools,
                    # On the last turn leave no choice: submit the result
                    force_tool=request.terminal_name if last_turn else None,
                    emit=emit,
                )
            except Exception as exc:  # noqa: BLE001 — surfaced upward as the item status
                return AgentOutcome(payload=None, usage=usage, turns=turns, error=_describe(exc))

            usage = usage + _extract_usage(completion)
            choice = completion.choices[0]
            message = choice.message
            tool_calls = list(message.tool_calls or [])

            if not tool_calls:
                payload = _payload_from_text(message.content)
                if payload is not None:
                    emit("fallback", "payload извлечён из текста ответа")
                    return AgentOutcome(payload=payload, usage=usage, turns=turns)
                if last_turn:
                    return AgentOutcome(
                        payload=None,
                        usage=usage,
                        turns=turns,
                        error=f"модель не вызвала {request.terminal_name} за {request.max_turns} ходов",
                    )
                messages.append({"role": "assistant", "content": message.content or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Продолжай работу и в конце обязательно вызови тул {request.terminal_name}.",
                    }
                )
                continue

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for call in tool_calls:
                fn_name = call.function.name
                args = parse_arguments(call.function.arguments or "")

                if fn_name == request.terminal_name:
                    emit("submit", f"ход {turn}")
                    return AgentOutcome(payload=args, usage=usage, turns=turns)

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
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

        return AgentOutcome(
            payload=None, usage=usage, turns=turns, error="исчерпан лимит ходов"
        )

    # ----------------------------------------------------------------- private

    async def _complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        force_tool: str | None,
        emit: Any,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "temperature": self._provider.temperature,
            "max_tokens": self._provider.max_tokens,
            "extra_headers": self._headers,
        }
        if force_tool:
            kwargs["tool_choice"] = self._provider.terminal_tool_choice_value(force_tool)
        else:
            kwargs["tool_choice"] = "auto"
        if not self._provider.parallel_tool_calls:
            kwargs["parallel_tool_calls"] = False

        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(1, self._provider.max_retries + 1):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except (RateLimitError, APITimeoutError) as exc:
                last_exc = exc
                emit("retry", f"попытка {attempt}/{self._provider.max_retries}: {type(exc).__name__}")
            except APIError as exc:
                status = getattr(exc, "status_code", None)
                # 4xx other than 429 is our own fault; retrying will not help
                if status is not None and 400 <= status < 500 and status != 429:
                    raise
                last_exc = exc
                emit("retry", f"попытка {attempt}/{self._provider.max_retries}: HTTP {status}")

            if attempt < self._provider.max_retries:
                await asyncio.sleep(delay)
                delay *= 2

        assert last_exc is not None
        raise last_exc


def _describe(exc: Exception) -> str:
    """A provider error phrased so it is clear what to do next."""
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        detail = str(getattr(exc, "message", "") or exc)[:300]
        if status in (401, 403):
            return (
                f"HTTP {status}: провайдер не принял ключ ({detail}). "
                "Разбирайся через `robotviewer --check-provider`"
            )
        if status == 404:
            return f"HTTP 404: эндпоинт или модель не найдены ({detail}). Проверь base_url и model"
        return f"HTTP {status}: {detail}"
    return f"{type(exc).__name__}: {exc}"


def _extract_usage(completion: Any) -> Usage:
    raw = getattr(completion, "usage", None)
    if raw is None:
        return Usage()
    return Usage(
        prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
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
