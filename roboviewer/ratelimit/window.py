"""One budget's accounting, filled either by us or by the gateway.

A window is one bucket's worth of "how much of the minute is gone". There are
two ways to know that, and which one is in force is not a mode anyone selects —
only whether the gateway answered the question.

Splitting this out of the limiter is not tidiness: the thing that decides *who
waits* and the thing that knows *how full a bucket is* have different reasons to
change. This one changes when a gateway starts or stops reporting; the limiter
changes when the run's shape does.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .metering import Allowance


class Window:
    """One budget over one stretch of time.

    Local entries are kept as mutable pairs so a reservation can rewrite its own
    amount once the response reports the real one; the total is summed on demand
    rather than carried, because a running total and an expiring deque disagree
    the first time a correction lands on an entry that has already aged out.
    """

    def __init__(self, capacity: int, window_s: float) -> None:
        self.capacity = capacity
        self._window_s = window_s
        self._entries: deque[list[float]] = deque()
        self._reported: _Reported | None = None

    def delay(self, amount: float, now: float) -> float:
        """How long before this much may be charged. Zero means go."""
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
        """Charge an estimate, and hand back the entry so it can be corrected."""
        entry = [now, amount]
        if self.capacity > 0:
            self._entries.append(entry)
        return entry

    def take(self, allowance: Allowance, now: float) -> None:
        """Adopt what the gateway says is left, and drop what we had counted.

        A remainder means nothing without a ceiling to subtract it from — a
        gateway that sends one and not the other has said how much is left of a
        number we do not have, so there is nothing to do with it.

        What this drops is charges made *before* the report, so requests already
        in flight when it was taken are missed. Bounded by the concurrency, and
        corrected by the next response — which, on a gateway that reports at
        all, is a second away.
        """
        if allowance.remaining is None or self.capacity <= 0:
            return
        window = allowance.reset_s if allowance.reset_s is not None else self._window_s
        self._reported = _Reported(
            used=max(0.0, self.capacity - allowance.remaining), reset_at=now + window
        )
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


@dataclass
class _Reported:
    """What the gateway last said was spent, and until when that holds."""

    used: float
    reset_at: float
