"""What one gateway meters, and what it will tell you about it.

Everything a run has to know about a provider's idea of load, stated as data.
Surveying ten hosted gateways in August 2026 turned up four families and no
fifth axis worth a branch in the code, so there are no subclasses here: a
dialect is a bucket list, a header template and two flags. `docs/rate-limits.md`
has the survey the table was built from.

The families, and why each one is not the others:

  * **openai** — requests and one combined token bucket, reported with limit,
    remaining and reset. OpenAI, Groq and Mistral all speak it, and so does most
    of what calls itself OpenAI-compatible. The default for anything unrecognised.
  * **fireworks** — prompt, uncached prompt and generated tokens, and the only
    family that advertises ceilings without ever saying how much is left. That
    silence is what forces the local accounting the limiter falls back on.
  * **anthropic** — requests, input and output, on its own header prefix and with
    reset as a timestamp rather than a duration. Two things are its own: cache
    reads do not count against input, and `max_tokens` does not count against
    output, so booking the ceiling would hold a run back for a charge that never
    arrives.
  * **none** — nothing is metered per key. DeepSeek limits concurrent
    connections instead, a self-hosted vLLM queues rather than refusing, and for
    both of those `run.concurrency` is the whole mechanism. Inventing a ceiling
    on their behalf only makes a run slower.

A gateway that reports what is left is the easy case, not a second algorithm:
`remaining` is an authoritative settling of the whole window, so the limiter
takes it and stops guessing. Predictive accounting is what happens when nobody
answers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any


# The quantity that feeds a bucket. Buckets are named per gateway; what fills
# them is not, which is why this is the thing the sending path talks about.
class Meter(Enum):
    REQUESTS = "requests"
    PROMPT = "prompt"  # every input token, cached or not
    UNCACHED = "uncached"  # input tokens the prefix cache did not serve
    GENERATED = "generated"  # output tokens, reasoning included
    TOTAL = "total"  # input and output charged to one bucket


@dataclass(frozen=True)
class Bucket:
    """One metered dimension.

    `name` is both the display name and the key under
    `[provider.rate_limits.per_minute]`, so what a person writes in a config is
    the same word the console prints back.
    """

    name: str
    meter: Meter
    # Substituted into the dialect's header template. Empty means this bucket is
    # never advertised, whatever the gateway says about the others.
    slug: str = ""


@dataclass(frozen=True)
class Shape:
    """A request, as much of it as is known before it is sent."""

    prompt: int
    output_ceiling: int


@dataclass(frozen=True)
class Spent:
    """What the answer said the request actually cost."""

    prompt: int
    uncached: int
    generated: int


@dataclass
class Allowance:
    """One bucket, as the gateway described it on the way back.

    Three independent pieces of news, and most gateways send some but not all:
    the ceiling, how much of it is left, and when it comes back. `None` is
    "not said", which is never the same as zero.
    """

    limit: int | None = None
    remaining: int | None = None
    reset_s: float | None = None


@dataclass(frozen=True)
class Dialect:
    """One gateway family's answer to what is metered and what is reported."""

    name: str
    buckets: tuple[Bucket, ...] = ()
    # How long the accounting window is. Not a constant: one family replenishes
    # continuously and another enforces on sub-minute windows, and a run that
    # respects the wrong one is refused while its own arithmetic says it is fine.
    window_s: float = 60.0
    # Template over {facet} and {slug}; empty means the gateway says nothing.
    header: str = ""
    # Which of limit / remaining / reset actually arrive.
    facets: tuple[str, ...] = ()
    # Whether to book `max_tokens` against the output bucket before sending.
    # False where the gateway charges only what was generated: booking a ceiling
    # it never charges is a hold bought for nothing. The budget is then enforced
    # after the fact — by what settling charges, and mainly by the remainder the
    # same gateway reports, which is the only figure that reflects real output.
    reserves_output: bool = True
    # One line, printed by --check-provider next to the name.
    why: str = ""

    @property
    def paces(self) -> bool:
        """Whether this gateway is metered at all. The 429 cooldown applies
        either way — a refusal is a refusal — but nothing is held back for a
        budget that does not exist."""
        return bool(self.buckets)

    @property
    def reports_remaining(self) -> bool:
        """Whether the run can be paced from what the gateway says rather than
        from what it guessed. The difference decides how much the estimate
        matters: not at all, or entirely."""
        return "remaining" in self.facets

    def bucket(self, name: str) -> Bucket | None:
        return next((b for b in self.buckets if b.name == name), None)

    def names(self) -> tuple[str, ...]:
        return tuple(b.name for b in self.buckets)


REQUESTS = "requests"
TOKENS = "tokens"
PROMPT = "prompt tokens"
UNCACHED = "uncached prompt tokens"
GENERATED = "generated tokens"
INPUT = "input tokens"
OUTPUT = "output tokens"

OPENAI = Dialect(
    name="openai",
    buckets=(
        Bucket(REQUESTS, Meter.REQUESTS, "requests"),
        Bucket(TOKENS, Meter.TOTAL, "tokens"),
    ),
    header="x-ratelimit-{facet}-{slug}",
    facets=("limit", "remaining", "reset"),
    why="requests and one combined token bucket, with what is left reported on every answer",
)

FIREWORKS = Dialect(
    name="fireworks",
    buckets=(
        Bucket(PROMPT, Meter.PROMPT, "prompt"),
        Bucket(UNCACHED, Meter.UNCACHED, "cache-adjusted-prompt"),
        Bucket(GENERATED, Meter.GENERATED, "generated"),
        Bucket(REQUESTS, Meter.REQUESTS),
    ),
    header="x-ratelimit-{facet}-tokens-{slug}",
    facets=("limit",),
    why="three token buckets; ceilings advertised but never the remainder, so the run keeps count",
)

ANTHROPIC = Dialect(
    name="anthropic",
    buckets=(
        Bucket(REQUESTS, Meter.REQUESTS, "requests"),
        # Cache reads are excluded from this budget, which is the same idea as
        # Fireworks' cache-adjusted prompt under a different name.
        Bucket(INPUT, Meter.UNCACHED, "input-tokens"),
        Bucket(OUTPUT, Meter.GENERATED, "output-tokens"),
    ),
    header="anthropic-ratelimit-{slug}-{facet}",
    facets=("limit", "remaining", "reset"),
    # Documented: max_tokens does not factor into the output budget, which is
    # evaluated against what is actually produced.
    reserves_output=False,
    why="requests, input and output; cache reads are free and max_tokens is not charged",
)

NONE = Dialect(
    name="none",
    why="nothing is metered per key; run.concurrency is the whole mechanism",
)

KNOWN: dict[str, Dialect] = {d.name: d for d in (OPENAI, FIREWORKS, ANTHROPIC, NONE)}

# Matched against base_url, longest host fragment first. Only the gateways whose
# metering differs from the OpenAI-compatible default need an entry: everything
# else is better served by the default than by a guess.
BY_HOST: tuple[tuple[str, Dialect], ...] = (
    ("fireworks.ai", FIREWORKS),
    ("api.anthropic.com", ANTHROPIC),
    ("api.claude.com", ANTHROPIC),
    ("api.deepseek.com", NONE),
    ("localhost", NONE),
    ("127.0.0.1", NONE),
)


def resolve(choice: str, base_url: str) -> tuple[Dialect, str]:
    """(dialect, why it was chosen). Named explicitly, or read off `base_url`.

    Resolved before the first request rather than from the first response, so
    the buckets are known while the config is still being validated — a ceiling
    written under a bucket the gateway does not meter should be refused at load
    time, not silently ignored an hour into a run.
    """
    if choice and choice != "auto":
        return KNOWN[choice], "named in the config"
    host = base_url.lower()
    for fragment, dialect in BY_HOST:
        if fragment in host:
            return dialect, f"matched {fragment} in base_url"
    return OPENAI, "no rule matched base_url, assuming OpenAI-compatible"


def estimate(dialect: Dialect, shape: Shape) -> dict[str, float]:
    """What to charge each bucket before the answer exists.

    The prompt is what it is. Output is the open question, and the two honest
    answers are the ceiling the request carries or nothing at all — which of
    them is right is the gateway's business, not this function's.

    Booking nothing means the output budget cannot hold a request back before it
    is sent; it bites once settling has actually overspent it, and before that
    the reported remainder is what paces the run. Booking a closer guess than
    either is a change of its own — see TASK-29.
    """
    output = float(shape.output_ceiling) if dialect.reserves_output else 0.0
    amounts = {
        Meter.REQUESTS: 1.0,
        Meter.PROMPT: float(shape.prompt),
        # Unknown before the fact: assume the cache serves nothing, correct after
        Meter.UNCACHED: float(shape.prompt),
        Meter.GENERATED: output,
        Meter.TOTAL: float(shape.prompt) + output,
    }
    return {bucket.name: amounts[bucket.meter] for bucket in dialect.buckets}


def actual(dialect: Dialect, spent: Spent) -> dict[str, float]:
    """What the answer says the request really cost, per bucket."""
    amounts = {
        Meter.REQUESTS: 1.0,
        Meter.PROMPT: float(spent.prompt),
        Meter.UNCACHED: float(spent.uncached),
        Meter.GENERATED: float(spent.generated),
        Meter.TOTAL: float(spent.prompt + spent.generated),
    }
    return {bucket.name: amounts[bucket.meter] for bucket in dialect.buckets}


def read(dialect: Dialect, headers: Any) -> dict[str, Allowance]:
    """What the gateway said about its own limits on the way back.

    Buckets it said nothing about are absent rather than present and empty: the
    caller has to be able to tell "no news" from "none left".
    """
    if not dialect.header or headers is None:
        return {}
    found: dict[str, Allowance] = {}
    for bucket in dialect.buckets:
        if not bucket.slug:
            continue
        allowance = _allowance(dialect, bucket, headers)
        if allowance is not None:
            found[bucket.name] = allowance
    return found


def retry_after(exc: Exception) -> float | None:
    """The gateway's own answer to "when should I come back", if it gave one."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    raw = _header(headers, "retry-after")
    return None if raw is None else _seconds(raw)


def estimate_tokens(payload: Any) -> int:
    """What a request will cost the prompt budget, before the gateway says.

    Four characters to the token is the usual rule of thumb and wrong by a tenth
    or so. That is fine here: the number only has to stop a burst from being
    planned, and it is replaced by the reported count a moment later. Against a
    gateway that reports what is left, it barely matters at all.
    """
    try:
        text = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return 0
    return len(text) // _CHARS_PER_TOKEN


_CHARS_PER_TOKEN = 4

# "6m0s", "1h2m3s", "1.5s" — the duration spelling several gateways use for reset
_DURATION = re.compile(
    r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?$"
)


def _allowance(dialect: Dialect, bucket: Bucket, headers: Any) -> Allowance | None:
    raw = {
        facet: _header(headers, dialect.header.format(facet=facet, slug=bucket.slug))
        for facet in dialect.facets
    }
    if not any(value is not None for value in raw.values()):
        return None
    reset = raw.get("reset")
    return Allowance(
        limit=_positive_int(raw.get("limit")),
        remaining=_non_negative_int(raw.get("remaining")),
        reset_s=_seconds(reset) if reset else None,
    )


def _header(headers: Any, name: str) -> str | None:
    """Case-insensitively, because httpx's mapping is and a plain dict is not."""
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        wanted = name.lower()
        for key, candidate in dict(headers).items():
            if str(key).lower() == wanted:
                return str(candidate)
    return None if value is None else str(value)


def _positive_int(raw: str | None) -> int | None:
    value = _int(raw)
    return value if value is not None and value > 0 else None


def _non_negative_int(raw: str | None) -> int | None:
    value = _int(raw)
    return value if value is not None and value >= 0 else None


def _int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _seconds(raw: str) -> float | None:
    """A reset or retry-after, in whichever of three spellings arrived.

    Plain seconds, a duration like "6m0s", or an RFC 3339 timestamp. A gateway
    that sends something else is better answered by the caller's own backoff
    than by a guess, so this says nothing rather than inventing a delay.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    if (delta := _duration(text)) is not None:
        return delta
    return _until(text)


def _duration(text: str) -> float | None:
    match = _DURATION.fullmatch(text)
    if match is None or not any(match.groups()):
        return None
    hours, minutes, secs, millis = (float(g or 0) for g in match.groups())
    return hours * 3600 + minutes * 60 + secs + millis / 1000


def _until(text: str) -> float | None:
    """An absolute reset time, turned into the wait it implies.

    Wall-clock rather than the injected monotonic clock, because the value being
    read is wall-clock and there is nothing to compare it to otherwise. The
    result is a duration, which is what everything downstream wants.
    """
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
