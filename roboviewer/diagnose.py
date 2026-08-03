"""Provider diagnostics: one minimal request with the response fully broken down.

Needed when a run dies on authentication or on gateway incompatibility: instead of
eight parallel agents exactly one call is made, and the raw status, response body
and headers are surfaced — usually the thing that explains a 401.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from openai import APIError, APIStatusError, AsyncOpenAI

from .config import ProviderConfig

_SECRET_HEADERS = ("authorization", "api-key", "x-api-key", "token", "cookie", "secret")

_HINTS: dict[int, str] = {
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
    403: "The key was accepted, but this model is not available to it. Check the model name and the key's permissions.",
    404: (
        "Endpoint not found. Most often base_url is missing /v1, or conversely "
        "carries a stray /chat/completions at the end."
    ),
    422: "The gateway rejected the request schema. Check the model name and max_tokens.",
    429: "Rate limited. The key works — this is a quota.",
}


def _mask_value(name: str, value: str) -> str:
    """Mask secrets while keeping their shape: the scheme and the key's tail stay
    visible, so "wrong key" can be told apart from "wrong auth scheme"."""
    if not any(marker in name.lower() for marker in _SECRET_HEADERS):
        return value
    scheme, _, secret = value.partition(" ")
    if not secret:  # header without a scheme: the whole value is the key
        return f"{value[:4]}…{value[-4:]}" if len(value) > 12 else "***"
    tail = f"{secret[:4]}…{secret[-4:]}" if len(secret) > 12 else "***"
    return f"{scheme} {tail}"


def _mask_headers(headers: dict[str, str]) -> dict[str, str]:
    return {name: _mask_value(name, value) for name, value in headers.items()}


class _Wire:
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

    def dump(self) -> None:
        print("Request as it went over the wire:")
        print(f"  {self.method} {self.url}")
        skip = ("host", "accept-encoding", "connection", "content-length", "accept", "user-agent")
        for name, value in _mask_headers(self.request_headers).items():
            low = name.lower()
            # x-stainless-* is SDK telemetry, unrelated to authentication
            if low in skip or low.startswith("x-stainless-"):
                continue
            print(f"  {name}: {value}")
        if self.status is not None:
            print()
            print(f"Response: HTTP {self.status}")
            for name in ("www-authenticate", "x-request-id", "x-error", "server", "content-type"):
                if name in self.response_headers:
                    print(f"  {name}: {self.response_headers[name]}")


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

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def content_looks_like_call(self) -> bool:
        """The model "called" the tool as text — what gateways without tool_call parsing do."""
        probe = self.content.strip()
        return bool(probe) and ("pong" in probe and ("{" in probe or "<" in probe))

    def summary(self) -> str:
        if self.error:
            return f"error — {self.error}"
        if self.tool_calls:
            return f"tool_calls: {', '.join(self.tool_calls)}"
        if self.legacy_function_call:
            return f"legacy function_call field: {self.legacy_function_call}"
        text = self.content.strip().replace("\n", " ")[:90] or "(empty)"
        marker = "text that looks like a call" if self.content_looks_like_call else "plain text"
        return f"{marker} · finish_reason={self.finish_reason} · {text}"


async def _request(provider: ProviderConfig, *, tools: bool, tool_choice: Any,
                   wire: "_Wire | None" = None) -> ProbeResult:
    timeout = min(provider.timeout_s, 60.0)
    client = AsyncOpenAI(
        api_key=provider.resolve_api_key(),
        base_url=provider.base_url,
        timeout=timeout,
        max_retries=0,
        http_client=wire.http_client(timeout) if wire else None,
    )
    kwargs: dict[str, Any] = {
        "model": provider.model,
        "messages": [{"role": "user", "content": "Call the pong tool with the word ping."
                      if tools else "ping"}],
        "max_tokens": 64,
        "extra_headers": provider.request_headers(),
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
        if exc.status_code in _HINTS:
            lines.append(f"Likely cause: {_HINTS[exc.status_code]}")
        result.error = "\n    ".join(lines)
        return result
    except (APIError, Exception) as exc:  # noqa: BLE001 — network, DNS, TLS
        result.error = _with_cause(exc)
        return result
    finally:
        await client.close()

    choice = completion.choices[0]
    message = choice.message
    result.finish_reason = choice.finish_reason
    result.content = message.content or ""
    result.tool_calls = [tc.function.name for tc in (message.tool_calls or [])]
    legacy = getattr(message, "function_call", None)
    if legacy is not None:
        result.legacy_function_call = getattr(legacy, "name", str(legacy))
    return result


def _print_auth_hints() -> None:
    print("Compare this with a request that works for you by hand. If the shape of")
    print("the authentication differs, set provider.auth_header / provider.auth_scheme:")
    print('  auth_header = "api-key",   auth_scheme = ""       → api-key: <key>')
    print('  auth_header = "X-Api-Key", auth_scheme = ""       → X-Api-Key: <key>')
    print('  auth_scheme = "Token"                             → Authorization: Token <key>')


async def _probe_all(provider: ProviderConfig, wire: _Wire) -> tuple[ProbeResult, dict[str, ProbeResult]]:
    """Plain request first; tool modes are only worth probing if it succeeded."""
    plain = await _request(provider, tools=False, tool_choice=None, wire=wire)
    if not plain.ok:
        return plain, {}

    modes: dict[str, ProbeResult] = {}
    for key, _, choice in TOOL_MODES:
        modes[key] = await _request(provider, tools=True, tool_choice=choice)
    return plain, modes


def _report_tool_modes(modes: dict[str, ProbeResult]) -> int:
    width = max(len(label) for _, label, _ in TOOL_MODES)
    for key, label, _ in TOOL_MODES:
        result = modes[key]
        mark = "✓" if result.tool_calls else "✗"
        print(f"   {mark} {label:<{width}}  {result.summary()}")

    working = [key for key, _, _ in TOOL_MODES if modes[key].tool_calls]
    print()

    if not working:
        print("Verdict: the gateway returned no real tool_call at all.")
        if any(modes[k].legacy_function_call for k in modes):
            print("  A legacy function_call field arrived instead of tool_calls — the gateway")
            print("  speaks the pre-June-2023 protocol. The reviewer will not understand it.")
        elif any(modes[k].content_looks_like_call for k in modes):
            print("  Instead of a call, text that looks like one arrived: the gateway does not")
            print("  parse tool_call, it just retells it in words. The reviewer will try to pull")
            print("  JSON out of the text, but it will not be reliable — findings will be lost.")
        else:
            print("  The model simply answered with text. Either it cannot do tool calling, or")
            print("  the gateway drops the tools field from the request.")
        print("  What to do: take a model that supports tool calling, or another gateway.")
        return 1

    # The reviewer needs "auto" during the run and the terminal mode on the last turn.
    if "auto" not in working:
        print("Verdict: tool calling works, but not with tool_choice = \"auto\".")
        print("  The reviewer runs on auto: it decides for itself whether to read files, and")
        print("  forces a call only on the last turn. This gateway will not do.")
        return 1

    best = next(key for key in ("forced", "required", "auto") if key in working)
    print("Verdict: the gateway supports tool calling.")
    if best == "forced":
        print('  The default setting fits: terminal_tool_choice = "forced".')
        return 0

    print('  But "forced" mode is not supported. Put this in the config:')
    print('    [provider]')
    print(f'    terminal_tool_choice = "{best}"')
    if best == "auto":
        print("  On auto the reviewer cannot make the agent submit its result on the last")
        print("  turn — some items will fail with \"the model never called submit_findings\".")
        print("  Raising max_turns helps.")
    return 0


def check_provider(provider: ProviderConfig) -> int:
    key, source = provider.api_key_source()

    print("Provider")
    print(f"  base_url       {provider.base_url}")
    print(f"  model          {provider.model}")
    print(f"  key            {provider.masked_key()}")
    print(f"  key source     {source}")
    print(f"  auth           {provider.auth_header}: "
          f"{(provider.auth_scheme + ' ') if provider.auth_scheme else ''}<key>")
    print(f"  submission     terminal_tool_choice = \"{provider.terminal_tool_choice}\"")
    if provider.extra_headers:
        print(f"  extra headers  {_mask_headers(provider.extra_headers)}")
    print()

    if key is None:
        print("✗ No key found — there is nothing to make a request with.")
        print(f"  Set provider.api_key in the config or the {provider.api_key_env} variable.")
        return 2
    if key != key.strip():
        print("⚠ The key has spaces or a newline at its edges — a common cause of 401.")

    wire = _Wire()
    plain, modes = asyncio.run(_probe_all(provider, wire))

    print("1. Plain request")
    if plain.ok:
        # For a plain request text is exactly what we want, so no call-shape verdict here
        print(f"   ✓ got an answer: {plain.content.strip()[:80] or '(empty)'}")
    else:
        print(f"   ✗ {plain.summary()}")
    if not plain.ok:
        print()
        wire.dump()
        print()
        _print_auth_hints()
        return 1

    print()
    print("2. Tool call — this is how the reviewer submits its result")
    return _report_tool_modes(modes)
