"""Who waits, and for how long.

The run's whole share of the gateway, held in one place. What is metered and
what the gateway reports about it is `metering`; how full one bucket is, is
`window`. This is left with the decision.

  * **One budget for the whole run.** Every agent reserves from the same
    limiter, because the gateway counts the run as a whole and so must we.

  * **A cooldown, always on.** When the gateway says 429 — or 503, which is the
    same message with a different number — every agent is held back, not just
    the one that was refused. The others are a moment away from the same answer,
    and hammering is what turns a busy minute into a failed run. This applies
    even to a gateway that meters nothing: a refusal is a refusal.

  * **Reserve at an estimate, settle at the truth.** The size of a request is
    guessed before sending and corrected the moment the answer arrives, so an
    estimate that was wrong costs one request's worth of drift rather than
    compounding.

Nothing here does any I/O, and the clock and the sleep are injected: a test can
run an hour of pacing in a millisecond.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .metering import Demand, Meter, Spent, actual, estimate, read
from .window import Window

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
    """The run's share of the gateway, shared by every agent.

    Budgets that are not shared are not budgets. What the buckets are, and
    whether the gateway will say how full they are, comes from the meter — this
    class only decides who waits.
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
            bucket.name: Window(configured.get(bucket.name, 0), meter.window_s)
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
        yesterday's number is wrong by design. A **remainder** replaces the
        run's own arithmetic outright: it is the one figure that also accounts
        for whatever else is spending the same key.

        Only ceilings are returned, because only they are worth announcing; a
        remainder changes on every response and saying so would be noise.
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
            window.take(allowance, now)
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
