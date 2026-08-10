"""Holding a run back so the gateway does not have to refuse it.

A review is a burst: several checklist items run at once, each resending a large
context every turn for minutes. Hosted gateways meter that and answer 429 when a
bucket is empty, and retrying into a full bucket does not help — the request has
to be held back before it is sent.

Three parts, because they change for three different reasons:

    metering    what one gateway counts and what it will tell you about it.
                Changes when a gateway is added or changes its headers.
    window      how full one bucket is, counted here or read off the gateway.
                Changes when the accounting does.
    limiter     who waits, and for how long. Changes when the run's shape does.

Only what a caller needs is re-exported here. `Window` is not: a bucket's
accounting is the limiter's business, and nothing outside should be adjusting
one by hand.

`docs/rate-limits.md` has the ten-gateway survey the four families were built
from, and the diagrams for the loop described above.
"""

from __future__ import annotations

from .limiter import RateLimiter, Reservation
from .metering import (
    ANTHROPIC,
    FAMILIES,
    FIREWORKS,
    GENERATED,
    INPUT,
    NONE,
    OPENAI,
    OUTPUT,
    PROMPT,
    REQUESTS,
    TOKENS,
    UNCACHED,
    Allowance,
    Bucket,
    Demand,
    Fills,
    Meter,
    Spent,
    estimate_tokens,
    resolve,
    retry_after,
)

# The budget (RateLimiter, Reservation), one request either side of the answer
# (Demand, Spent), what a gateway meters (Meter, Bucket, Fills, Allowance,
# resolve, FAMILIES), the four families, the bucket names that double as config
# keys, and the two readers of a response. Sorted because the linter says so;
# the grouping is in the docstring above.
__all__ = [
    "ANTHROPIC",
    "FAMILIES",
    "FIREWORKS",
    "GENERATED",
    "INPUT",
    "NONE",
    "OPENAI",
    "OUTPUT",
    "PROMPT",
    "REQUESTS",
    "TOKENS",
    "UNCACHED",
    "Allowance",
    "Bucket",
    "Demand",
    "Fills",
    "Meter",
    "RateLimiter",
    "Reservation",
    "Spent",
    "estimate_tokens",
    "resolve",
    "retry_after",
]
