"""Pacing a run so the provider does not have to say no.

A review is a burst: several checklist items run at once, each resending a large
context every turn. Serverless providers meter that per minute — Fireworks, for
one, counts prompt tokens, uncached prompt tokens and generated tokens
separately and answers 429 when a bucket is empty. Retrying into a full bucket
does not help; the request has to be held back before it is sent.

Two mechanisms, and only one of them needs configuring:

  * **A cooldown, always on.** When the provider says 429 (or 503, which is the
    same message with a different number), every agent is held back, not just
    the one that was refused. The others are a moment away from the same answer,
    and hammering is what turns a busy minute into a failed run.

  * **Per-minute ceilings, when they are known.** A sliding sixty-second window
    per bucket, so the run spreads itself out instead of arriving all at once.
    The numbers come from the config, or from the provider itself: Fireworks
    advertises the effective ceilings on every response and moves them with
    usage, so what it says beats a number typed here months ago.

The size of a request is estimated before sending — the exact count only exists
in the response — and corrected the moment the answer arrives, so an estimate
that was wrong costs one request's worth of drift rather than compounding.

Nothing here does any I/O, and the clock and the sleep are injected: a test can
run an hour of pacing in a millisecond.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import RateLimits

WINDOW_S = 60.0

# Rough on purpose. The estimate only has to stop a burst from being planned,
# and it is replaced by the reported count as soon as the response arrives.
CHARS_PER_TOKEN = 4

PROMPT = "prompt tokens"
UNCACHED = "uncached prompt tokens"
GENERATED = "generated tokens"
REQUESTS = "requests"
COOLDOWN = "the provider's 429"

# What Fireworks puts on every response. Read case-insensitively: httpx headers
# are, and a gateway that changes the casing is not changing the meaning.
ADVERTISED = {
    "x-ratelimit-limit-tokens-prompt": PROMPT,
    "x-ratelimit-limit-tokens-cache-adjusted-prompt": UNCACHED,
    "x-ratelimit-limit-tokens-generated": GENERATED,
}

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass
class Reservation:
    """A place in the minute, charged at an estimate until the truth arrives."""

    entries: dict[str, list[float]] = field(default_factory=dict)
    waited: float = 0.0
    reason: str = ""

    @property
    def held(self) -> bool:
        return self.waited > 0.0


class RateLimiter:
    """The whole run's share of the provider, held in one place.

    One of these per run, passed to every agent: budgets that are not shared are
    not budgets, since the provider counts the run as a whole.
    """

    def __init__(
        self,
        limits: RateLimits,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self._windows = {
            PROMPT: _Window(limits.prompt_tokens_per_minute),
            UNCACHED: _Window(limits.uncached_prompt_tokens_per_minute),
            GENERATED: _Window(limits.generated_tokens_per_minute),
            REQUESTS: _Window(limits.requests_per_minute),
        }

    async def reserve(self, *, prompt: int, generated: int) -> Reservation:
        """Wait until this request fits, then charge it at the estimate.

        `generated` is the cap the request carries rather than what it will use,
        which is the only honest guess before the fact and errs towards patience.
        """
        # Counted from what we deliberately slept, not from the clock: a request
        # that sailed through must report a wait of zero, and two readings of a
        # clock are never quite equal.
        waited = 0.0
        reason = ""
        while True:
            async with self._lock:
                now = self._clock()
                delay, blocked_by = self._delay(prompt, generated, now)
                if delay <= 0.0:
                    return Reservation(
                        entries=self._charge(prompt, generated, now),
                        waited=waited,
                        reason=reason,
                    )
                reason = blocked_by
            await self._sleep(delay)
            waited += delay

    def settle(
        self, reservation: Reservation, *, prompt: int, uncached: int, generated: int
    ) -> None:
        """Replace the estimates with what the response actually reported."""
        exact = {PROMPT: prompt, UNCACHED: uncached, GENERATED: generated}
        for name, amount in exact.items():
            entry = reservation.entries.get(name)
            if entry is not None:
                entry[1] = float(amount)

    def pause(self, seconds: float) -> float:
        """Hold every agent back for a while. Returns how long the hold now runs.

        Extends an existing hold rather than replacing it: three agents refused
        in the same second should not each shorten what the first one set.
        """
        until = self._clock() + max(0.0, seconds)
        self._cooldown_until = max(self._cooldown_until, until)
        return self._cooldown_until - self._clock()

    def observe(self, headers: Any) -> dict[str, int]:
        """Adopt the ceilings the provider advertises. Returns what changed.

        Only upwards-or-downwards from what was configured — the provider is the
        authority on its own limits, and on an adaptive plan yesterday's number
        is wrong by design.
        """
        if not self._limits.adopt_advertised or headers is None:
            return {}
        changed: dict[str, int] = {}
        for header, name in ADVERTISED.items():
            value = _int_header(headers, header)
            if value is None or value == self._windows[name].capacity:
                continue
            self._windows[name].capacity = value
            changed[name] = value
        return changed

    def _delay(self, prompt: int, generated: int, now: float) -> tuple[float, str]:
        """How long before this request may go, and what is holding it."""
        waits = [
            (self._cooldown_until - now, COOLDOWN),
            (self._windows[PROMPT].delay(prompt, now), PROMPT),
            # Unknown before the fact: assume nothing is cached, correct after
            (self._windows[UNCACHED].delay(prompt, now), UNCACHED),
            (self._windows[GENERATED].delay(generated, now), GENERATED),
            (self._windows[REQUESTS].delay(1, now), REQUESTS),
        ]
        return max(waits, key=lambda wait: wait[0])

    def _charge(self, prompt: int, generated: int, now: float) -> dict[str, list[float]]:
        estimates = {PROMPT: prompt, UNCACHED: prompt, GENERATED: generated, REQUESTS: 1}
        return {
            name: self._windows[name].add(float(amount), now)
            for name, amount in estimates.items()
        }


def estimate_tokens(payload: Any) -> int:
    """What a request will cost the prompt budget, before the provider says.

    Four characters to the token is the usual rule of thumb and wrong by a tenth
    or so; that is fine here, because the number is only used to decide whether
    to wait and is replaced by the reported count a moment later.
    """
    try:
        text = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return 0
    return len(text) // CHARS_PER_TOKEN


def retry_after(exc: Exception) -> float | None:
    """The provider's own answer to "when should I come back", if it gave one."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    raw = _header(headers, "retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        # The HTTP-date form. Rare from JSON APIs, and a date we cannot parse is
        # better answered by the caller's backoff than by guessing.
        return None


class _Window:
    """One sliding minute of one budget.

    Entries are kept as mutable pairs so a reservation can rewrite its own
    amount once the response reports the real one; the total is summed on demand
    rather than carried, because a running total and an expiring deque disagree
    the first time a correction lands on an entry that has already aged out.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._entries: deque[list[float]] = deque()

    def delay(self, amount: float, now: float) -> float:
        if self.capacity <= 0:
            return 0.0
        self._expire(now)
        room = self.capacity - self._total()
        if amount <= room:
            return 0.0
        if amount > self.capacity:
            # One request larger than the whole minute. Waiting cannot make it
            # fit, so let the provider be the one to refuse it.
            return 0.0
        needed = amount - room
        freed = 0.0
        for stamp, size in self._entries:
            freed += size
            if freed >= needed:
                return max(0.0, stamp + WINDOW_S - now)
        return 0.0

    def add(self, amount: float, now: float) -> list[float]:
        entry = [now, amount]
        if self.capacity > 0:
            self._entries.append(entry)
        return entry

    def _total(self) -> float:
        return sum(amount for _, amount in self._entries)

    def _expire(self, now: float) -> None:
        while self._entries and self._entries[0][0] + WINDOW_S <= now:
            self._entries.popleft()


def _header(headers: Any, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        # Not every mapping is case-insensitive the way httpx's is
        for key, candidate in dict(headers).items():
            if str(key).lower() == name:
                return str(candidate)
    return None if value is None else str(value)


def _int_header(headers: Any, name: str) -> int | None:
    raw = _header(headers, name)
    if raw is None:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        return None
    return value if value > 0 else None
