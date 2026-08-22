"""How the config shapes a request: auth headers, the thinking switch, and how
hard the agent is pushed on its final turn.

Kept apart from the config models because this is where the OpenAI SDK shows
through — the `Omit` sentinel, the `chat_template_kwargs` body field, the
`tool_choice` shapes — and a settings file should be readable without knowing
any of that.
"""

from __future__ import annotations

from typing import Any

from ..config import ModelConfig, ProviderConfig

try:  # private SDK API: the only way to drop the built-in Authorization header
    from openai._types import Omit as _OmitType

    _OMIT: Any = _OmitType()
except ImportError:  # pragma: no cover — in case SDK internals change
    _OMIT = None


def request_headers(provider: ProviderConfig) -> dict[str, Any]:
    """Headers attached to every request.

    Passed per-request rather than via default_headers: dropping the built-in
    Authorization header is only possible with the Omit sentinel, and the SDK
    client constructor rejects it.
    """
    headers: dict[str, Any] = dict(provider.extra_headers)
    headers[provider.auth_header] = auth_value(provider)
    if provider.auth_header.lower() != "authorization" and _OMIT is not None:
        headers["Authorization"] = _OMIT
    return headers


def auth_value(provider: ProviderConfig) -> str:
    key = provider.resolve_api_key()
    return f"{provider.auth_scheme} {key}" if provider.auth_scheme else key


def request_body(model: ModelConfig) -> dict[str, Any]:
    """The body fields the SDK has no parameter for: `extra_body` as written,
    with the thinking switch laid over it. Rebuilt per request — the config is
    never mutated."""
    body: dict[str, Any] = dict(model.extra_body)
    if model.enable_thinking is not None:
        template_kwargs = dict(body.get("chat_template_kwargs") or {})
        template_kwargs["enable_thinking"] = model.enable_thinking
        body["chat_template_kwargs"] = template_kwargs
    return body


def terminal_tool_choice(provider: ProviderConfig, tool_name: str) -> Any:
    """The `tool_choice` value for the last turn, in the strongest form the
    gateway was said to accept."""
    if provider.terminal_tool_choice == "forced":
        return {"type": "function", "function": {"name": tool_name}}
    return provider.terminal_tool_choice  # "required" / "auto"
