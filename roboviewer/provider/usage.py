"""Reading token usage out of whatever shape the gateway returned it in.

Gateways hand usage back as objects or as plain dicts, name the prefix-cache
field three different ways, and sometimes leave it out — which is not the same
as reporting zero. All of that is settled here, once, into `models.Usage`.
"""

from __future__ import annotations

from typing import Any

from ..models import Usage


def extract_usage(completion: Any) -> Usage:
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
