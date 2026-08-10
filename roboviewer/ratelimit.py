"""Pacing a run so the gateway does not have to say no.

A review is a burst: several checklist items run at once, each resending a large
context every turn. Hosted gateways meter that and answer 429 when a bucket is
empty. Retrying into a full bucket does not help; the request has to be held
back before it is sent.

What is metered and what the gateway reports about it lives in `metering`. What
is here is the part that is the same everywhere:

  * **One budget for the whole run.** Every agent reserves from the same
    limiter, because the gateway counts the run as a whole and so must we.

  * **A cooldown, always on.** When the gateway says 429 (or 503, which is the
    same message with a different number), every agent is held back, not just
    the one that was refused. The others are a moment away from the same answer,
    and hammering is what turns a busy minute into a failed run. This applies
    even to a gateway that meters nothing — a refusal is a refusal.

  * **A window per bucket**, filled one of two ways. If the gateway reports what
    is left, that is taken as the truth and the run stops guessing. If it
    reports only a ceiling, the run keeps its own count: the size of a request
    is estimated before sending and corrected the moment the answer arrives, so
    an estimate that was wrong costs one request's worth of drift rather than
    compounding.

Nothing here does any I/O, and the clock and the sleep are injected: a test can
run an hour of pacing in a millisecond.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .metering import Allowance, Demand, Meter, Spent, actual, estimate, read

COOLDOWN = "the gateway's 429"

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass
class Reservation:
    """A place in the window, charged at an estimate until the truth arrives."""

    entries: dict[str, list[float]] = field(default_factory=dict)
    waited: float = 0.0
    reason: str = ""

    @property
    def held(self) -> bool:
        return self.waited > 0.0


class RateLimiter:
    """The whole run's share of the gateway, held in one place.

    One of these per run, passed to every agent: budgets that are not shared are
    not budgets. What the buckets are, and whether the gateway will say how full
    they are, comes from the meter — this class only decides who waits.
    """

    def __init__(
        self,
        meter: Meter,
        ceilings: dict[str, int] | None = None,
        *,
        adopt_advertised: bool = True,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._meter = meter
        self._adopt = adopt_advertised
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._cooldown_until = 0.0
        configured = ceilings or {}
        self._windows = {
            bucket.name: _Window(configured.get(bucket.name, 0), meter.window_s)
            for bucket in meter.buckets
        }

    async def reserve(self, demand: Demand) -> Reservation:
        """Wait until this request fits, then charge it at the estimate."""
        # Counted from what we deliberately slept, not from the clock: a request
        # that sailed through must report a wait of zero, and two readings of a
        # clock are never quite equal.
        waited = 0.0
        reason = ""
        wanted = estimate(self._meter, demand)
        while True:
            async with self._lock:
                now = self._clock()
                delay, blocked_by = self._delay(wanted, now)
                if delay <= 0.0:
                    return Reservation(
                        entries=self._charge(wanted, now), waited=waited, reason=reason
                    )
                reason = blocked_by
            await self._sleep(delay)
            waited += delay

    def settle(self, reservation: Reservation, spent: Spent) -> None:
        """Replace the estimates with what the response actually reported."""
        for name, amount in actual(self._meter, spent).items():
            entry = reservation.entries.get(name)
            if entry is not None:
                entry[1] = amount

    def pause(self, seconds: float) -> float:
        """Hold every agent back for a while. Returns how long the hold now runs.

        Extends an existing hold rather than replacing it: three agents refused
        in the same second should not each shorten what the first one set.
        """
        until = self._clock() + max(0.0, seconds)
        self._cooldown_until = max(self._cooldown_until, until)
        return self._cooldown_until - self._clock()

    def observe(self, headers: object) -> dict[str, int]:
        """Take the gateway at its word. Returns the ceilings that changed.

        Two different pieces of news arrive on the same response. A **ceiling**
        replaces what was configured, in either direction — on an adaptive plan
        yesterday's number is wrong by design, and a figure typed into a config
        months ago is not the authority on today's account. A **remainder**
        replaces the run's own arithmetic outright: it is the one number that
        also accounts for whatever else is spending the same key.

        Only ceilings are returned, because only they are worth announcing; a
        remainder changes every single response and saying so would be noise.
        """
        if not self._adopt:
            return {}
        changed: dict[str, int] = {}
        now = self._clock()
        for name, allowance in read(self._meter, headers).items():
            window = self._windows.get(name)
            if window is None:
                continue
            if allowance.limit is not None and allowance.limit != window.capacity:
                window.capacity = allowance.limit
                changed[name] = allowance.limit
            _apply(window, allowance, now)
        return changed

    def _delay(self, wanted: dict[str, float], now: float) -> tuple[float, str]:
        """How long before this request may go, and what is holding it."""
        waits = [(self._cooldown_until - now, COOLDOWN)]
        waits += [
            (self._windows[name].delay(amount, now), name) for name, amount in wanted.items()
        ]
        return max(waits, key=lambda wait: wait[0])

    def _charge(self, wanted: dict[str, float], now: float) -> dict[str, list[float]]:
        return {name: self._windows[name].add(amount, now) for name, amount in wanted.items()}


@dataclass
class _Reported:
    """What the gateway last said was left, and until when it holds."""

    used: float
    at: float
    reset_at: float


class _Window:
    """One budget, either counted here or read off the gateway.

    Local entries are kept as mutable pairs so a reservation can rewrite its own
    amount once the response reports the real one; the total is summed on demand
    rather than carried, because a running total and an expiring deque disagree
    the first time a correction lands on an entry that has already aged out.

    When the gateway reports a remainder, that becomes the floor and only
    charges made *since* it are added on top. Requests already in flight when
    the report was taken are therefore missed — bounded by the concurrency, and
    corrected by the next response, which is a second away on a gateway that
    reports at all.
    """

    def __init__(self, capacity: int, window_s: float) -> None:
        self.capacity = capacity
        self._window_s = window_s
        self._entries: deque[list[float]] = deque()
        self._reported: _Reported | None = None

    def delay(self, amount: float, now: float) -> float:
        if self.capacity <= 0:
            return 0.0
        room = self.capacity - self._total(now)
        if amount <= room and room > 0:
            return 0.0
        # An exhausted bucket holds a request back even when the request books
        # nothing against it. Where the output ceiling is not charged the
        # booking is zero every time, and "zero fits in nothing" would let a run
        # walk straight into a budget the gateway has already reported empty.
        if amount > self.capacity:
            # One request larger than the whole window. Waiting cannot make it
            # fit, so let the gateway be the one to refuse it.
            return 0.0
        if (until := self._reset_at(now)) is not None:
            # The gateway named the moment its budget comes back. It knows and
            # we are guessing, so its answer wins over any local arithmetic.
            return until - now
        needed = amount - room
        freed = 0.0
        for stamp, size in self._entries:
            freed += size
            if freed >= needed:
                return max(0.0, stamp + self._window_s - now)
        return 0.0

    def add(self, amount: float, now: float) -> list[float]:
        entry = [now, amount]
        if self.capacity > 0:
            self._entries.append(entry)
        return entry

    def report(self, used: float, reset_s: float | None, now: float) -> None:
        """Adopt what the gateway says is spent, and drop what we had counted."""
        reset_at = now + (reset_s if reset_s is not None else self._window_s)
        self._reported = _Reported(used=max(0.0, used), at=now, reset_at=reset_at)
        self._entries.clear()

    def _total(self, now: float) -> float:
        self._expire(now)
        local = sum(amount for _, amount in self._entries)
        if self._reported is None or now >= self._reported.reset_at:
            return local
        return self._reported.used + local

    def _reset_at(self, now: float) -> float | None:
        if self._reported is None or now >= self._reported.reset_at:
            return None
        return self._reported.reset_at

    def _expire(self, now: float) -> None:
        while self._entries and self._entries[0][0] + self._window_s <= now:
            self._entries.popleft()


def _apply(window: _Window, allowance: Allowance, now: float) -> None:
    """A reported remainder, turned into the window's own terms.

    A remainder means nothing without a ceiling to subtract it from — a gateway
    that sends one and not the other has said how much is left of a number we do
    not have, so there is nothing to do with it.
    """
    if allowance.remaining is None or window.capacity <= 0:
        return
    window.report(window.capacity - allowance.remaining, allowance.reset_s, now)
