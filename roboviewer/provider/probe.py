"""One minimal request to the gateway, with the answer fully broken down.

Needed when a run dies on authentication or on gateway incompatibility: instead
of eight parallel agents exactly one call is made, and the raw status, response
body and headers are surfaced — usually the thing that explains a 401.

What the probes found is data; what to tell the user about it is
`cli.check_provider`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from openai import APIError, APIStatusError, AsyncOpenAI
from openai.types.chat.chat_completion import Choice

from ..config import ProviderConfig
from .request import request_headers

SECRET_HEADERS = ("authorization", "api-key", "x-api-key", "token", "cookie", "secret")

# Reasoning models spend tokens on reasoning_content before any answer or
# tool_call; a small budget cuts them off at finish_reason=length, which looks
# exactly like "cannot call tools". 64 was enough to lose every reasoning model.
MAX_TOKENS = 4096

# Where gateways put the reasoning text on the message, by dialect
REASONING_FIELDS = ("reasoning_content", "reasoning")

HINTS: dict[int, str] = {
    400: (
        "The gateway rejected the request, usually over a field it does not know. "
        "Candidates: parallel_tool_calls (set it to false) and tool_choice "
        "(see terminal_tool_choice below)."
    ),
    401: (
        "Authentication failed. If the key definitely works, it is usually how the key "
        "is passed: the SDK sends Authorization: Bearer <key> by default, while the "
        "gateway may expect api-key, X-Api-Key or another scheme — see "
        "provider.auth_header and provider.auth_scheme. Compare the request dump below "
        "with a request that works by hand."
    ),
    403: (
        "The key was accepted, but this model is not available to it. "
        "Check the model name and the key's permissions."
    ),
    404: (
        "Endpoint not found. Most often base_url is missing /v1, or conversely "
        "carries a stray /chat/completions at the end."
    ),
    422: "The gateway rejected the request schema. Check the model name and max_tokens.",
    429: "Rate limited. The key works — this is a quota.",
}

PONG_TOOL = {
    "type": "function",
    "function": {
        "name": "pong",
        "description": "Answer by calling this tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Any word"}},
            "required": ["text"],
        },
    },
}

# tool_choice variants that gateways support to varying degrees
TOOL_MODES: list[tuple[str, str, Any]] = [
    ("auto", 'tool_choice = "auto"', "auto"),
    ("required", 'tool_choice = "required"', "required"),
    ("forced", "tool_choice = {function}", {"type": "function", "function": {"name": "pong"}}),
]


class ProbeResult:
    def __init__(self) -> None:
        self.error: str | None = None
        self.tool_calls: list[str] = []
        self.legacy_function_call: str | None = None
        self.finish_reason: str | None = None
        self.content: str = ""
        self.reasoning: str = ""

    @classmethod
    def from_choice(cls, choice: Choice) -> ProbeResult:
        result = cls()
        message = choice.message
        result.finish_reason = choice.finish_reason
        result.content = message.content or ""
        result.tool_calls = [
            tc.function.name for tc in (message.tool_calls or []) if tc.type == "function"
        ]
        legacy = getattr(message, "function_call", None)
        if legacy is not None:
            result.legacy_function_call = getattr(legacy, "name", str(legacy))
        for field in REASONING_FIELDS:
            value = getattr(message, field, None)
            if value:
                result.reasoning = str(value)
                break
        return result

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def content_looks_like_call(self) -> bool:
        """The model "called" the tool as text — what gateways without tool_call parsing do."""
        probe = self.content.strip()
        return bool(probe) and ("pong" in probe and ("{" in probe or "<" in probe))

    @property
    def ran_out_while_reasoning(self) -> bool:
        """Cut off by max_tokens with the budget spent on reasoning — says nothing
        about tool calling, unlike an answer the model chose to give as text."""
        return self.finish_reason == "length" and not self.tool_calls and bool(self.reasoning)

    def summary(self) -> str:
        if self.error:
            return f"error — {self.error}"
        if self.tool_calls:
            return f"tool_calls: {', '.join(self.tool_calls)}"
        if self.legacy_function_call:
            return f"legacy function_call field: {self.legacy_function_call}"
        if self.ran_out_while_reasoning:
            return f"the whole {MAX_TOKENS}-token budget went to reasoning · finish_reason=length"
        text = self.content.strip().replace("\n", " ")[:90] or "(empty)"
        marker = "text that looks like a call" if self.content_looks_like_call else "plain text"
        return f"{marker} · finish_reason={self.finish_reason} · {text}"


class Wire:
    """What actually went over the wire and what came back."""

    def __init__(self) -> None:
        self.method = ""
        self.url = ""
        self.request_headers: dict[str, str] = {}
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}

    def http_client(self, timeout: float) -> httpx.AsyncClient:
        async def on_request(request: httpx.Request) -> None:
            self.method = request.method
            self.url = str(request.url)
            self.request_headers = dict(request.headers)

        async def on_response(response: httpx.Response) -> None:
            self.status = response.status_code
            self.response_headers = dict(response.headers)

        return httpx.AsyncClient(
            timeout=timeout,
            event_hooks={"request": [on_request], "response": [on_response]},
        )


async def probe_all(
    provider: ProviderConfig, model: str, wire: Wire
) -> tuple[ProbeResult, dict[str, ProbeResult]]:
    """Plain request first; tool modes are only worth probing if it succeeded."""
    plain = await probe(provider, model, tools=False, tool_choice=None, wire=wire)
    if not plain.ok:
        return plain, {}

    modes: dict[str, ProbeResult] = {}
    for key, _, choice in TOOL_MODES:
        modes[key] = await probe(provider, model, tools=True, tool_choice=choice)
    return plain, modes


async def probe(provider: ProviderConfig, model: str, *, tools: bool, tool_choice: Any,
                wire: Wire | None = None) -> ProbeResult:
    timeout = min(provider.timeout_s, 60.0)
    client = AsyncOpenAI(
        api_key=provider.resolve_api_key(),  # pragma: allowlist secret
        base_url=provider.base_url,
        timeout=timeout,
        max_retries=0,
        http_client=wire.http_client(timeout) if wire else None,
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Call the pong tool with the word ping."
                      if tools else "ping"}],
        "max_tokens": MAX_TOKENS,
        "extra_headers": request_headers(provider),
    }
    if tools:
        kwargs["tools"] = [PONG_TOOL]
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if not provider.parallel_tool_calls:
            kwargs["parallel_tool_calls"] = False

    result = ProbeResult()
    try:
        completion = await client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        lines = [f"HTTP {exc.status_code}", f"Response body: {_body_of(exc)}"]
        if exc.status_code in HINTS:
            lines.append(f"Likely cause: {HINTS[exc.status_code]}")
        result.error = "\n    ".join(lines)
        return result
    except (APIError, Exception) as exc:  # noqa: BLE001 — network, DNS, TLS
        result.error = _with_cause(exc)
        return result
    finally:
        await client.close()

    return ProbeResult.from_choice(completion.choices[0])


def mask_headers(headers: dict[str, str]) -> dict[str, str]:
    return {name: _mask_value(name, value) for name, value in headers.items()}


def _mask_value(name: str, value: str) -> str:
    """Mask secrets while keeping their shape: the scheme and the key's tail stay
    visible, so "wrong key" can be told apart from "wrong auth scheme"."""
    if not any(marker in name.lower() for marker in SECRET_HEADERS):
        return value
    scheme, _, secret = value.partition(" ")  # pragma: allowlist secret
    if not secret:  # header without a scheme: the whole value is the key
        return f"{value[:4]}…{value[-4:]}" if len(value) > 12 else "***"
    tail = f"{secret[:4]}…{secret[-4:]}" if len(secret) > 12 else "***"
    return f"{scheme} {tail}"


def _body_of(exc: Any) -> str:
    for attr in ("body", "message"):
        value = getattr(exc, attr, None)
        if value:
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, indent=2)[:2000]
            return str(value)[:2000]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            return response.text[:2000]
        except Exception:  # noqa: BLE001
            pass
    return "(empty response body)"


def _with_cause(exc: Exception) -> str:
    """The SDK wraps low-level failures into APIConnectionError('Connection error.'),
    which explains nothing. The real reason sits in __cause__."""
    text = f"{type(exc).__name__}: {exc}"
    cause = exc.__cause__ or exc.__context__
    if cause is not None and str(cause) and str(cause) not in str(exc):
        text += f"\n    Root cause: {type(cause).__name__}: {str(cause)[:300]}"
    return text
