"""Statistics over a benchmark's reviews: tokens, time, self-consistency.

A run that repeats every entry produces a sample per entry rather than one
number, and the summary reports the sample: what the runner and the judge
spent, how long a review took, and how much the repeats agree. One `Group` per
entry and one over the whole run; `run` renders them into the summary.

Findings are matched across repeats by file and line — both commits are fixed,
so a location is stable where the model's wording of a title is not. Two
repeats that both found nothing agree completely.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from math import ceil

from ..models import ReviewRun, Severity, Usage


@dataclass(frozen=True)
class Distribution:
    """How a per-review number is spread over the repeats."""

    mean: float
    p50: float
    p90: float
    max: float

    @classmethod
    def over(cls, values: list[float]) -> Distribution | None:
        if not values:
            return None
        return cls(
            mean=sum(values) / len(values),
            p50=percentile(values, 50),
            p90=percentile(values, 90),
            max=max(values),
        )


@dataclass(frozen=True)
class Review:
    """One finished review, in the terms the statistics are computed in."""

    run: ReviewRun
    seconds: float

    @property
    def runner_usage(self) -> Usage:
        acc = Usage()
        for item in self.run.items:
            acc = acc + item.usage
        return acc

    @property
    def judge_usage(self) -> Usage:
        return self.run.judge_usage


@dataclass(frozen=True)
class Group:
    """The statistics of one entry's repeats — or of the whole run."""

    title: str
    reviews: int
    runner: Usage
    judge: Usage
    runner_tokens: Distribution | None
    judge_tokens: Distribution | None
    seconds: Distribution | None
    # None when there are not two reviews to compare.
    consistency: float | None
    consistency_by_severity: dict[Severity, float | None]


def group(title: str, reviews: list[Review]) -> Group:
    """The statistics of one entry: totals, spreads, and how much the repeats
    agree with each other."""
    runs = [review.run for review in reviews]
    return Group(
        title=title,
        reviews=len(reviews),
        runner=_total(review.runner_usage for review in reviews),
        judge=_total(review.judge_usage for review in reviews),
        runner_tokens=Distribution.over([float(r.runner_usage.total_tokens) for r in reviews]),
        judge_tokens=Distribution.over([float(r.judge_usage.total_tokens) for r in reviews]),
        seconds=Distribution.over([review.seconds for review in reviews]),
        consistency=consistency(runs),
        consistency_by_severity={
            severity: consistency(runs, severity) for severity in Severity
        },
    )


def whole(title: str, groups: list[Group], reviews: list[Review]) -> Group:
    """The whole run: tokens and time pooled over every review, consistency
    averaged over the entries that have one — repeats of different entries are
    not comparable to each other."""
    pooled = group(title, reviews)
    return Group(
        title=title,
        reviews=pooled.reviews,
        runner=pooled.runner,
        judge=pooled.judge,
        runner_tokens=pooled.runner_tokens,
        judge_tokens=pooled.judge_tokens,
        seconds=pooled.seconds,
        consistency=_mean_of([g.consistency for g in groups]),
        consistency_by_severity={
            severity: _mean_of([g.consistency_by_severity[severity] for g in groups])
            for severity in Severity
        },
    )


def consistency(runs: list[ReviewRun], severity: Severity | None = None) -> float | None:
    """Mean pairwise Jaccard over the confirmed findings of the repeats,
    optionally of one severity only. Findings are keyed by file and line.

    None when there is nothing to compare: fewer than two repeats, or a
    severity no repeat found anything of — an unexercised level is not scored,
    where two repeats that both found nothing at all genuinely agree."""
    if len(runs) < 2:
        return None
    keys = [
        {
            (finding.file, finding.line)
            for finding in run.confirmed()
            if severity in (None, finding.severity)
        }
        for run in runs
    ]
    if severity is not None and not any(keys):
        return None
    pairs = list(combinations(keys, 2))
    return sum(_jaccard(a, b) for a, b in pairs) / len(pairs)


def percentile(values: list[float], p: int) -> float:
    """Nearest-rank: the smallest value at least p per cent of the sample does
    not exceed. Exact for the small sample sizes a benchmark run has."""
    if not values:
        raise ValueError("percentile of an empty sample")
    ordered = sorted(values)
    return ordered[max(ceil(p / 100 * len(ordered)), 1) - 1]


def payload(entry: Group) -> dict[str, object]:
    """The group as `summary.json` carries it."""
    return {
        "title": entry.title,
        "reviews": entry.reviews,
        "runner": _usage_payload(entry.runner, entry.runner_tokens),
        "judge": _usage_payload(entry.judge, entry.judge_tokens),
        "seconds": _distribution_payload(entry.seconds, digits=1),
        "consistency": {
            "all": _rounded(entry.consistency),
            "by_severity": {
                severity.value: _rounded(value)
                for severity, value in entry.consistency_by_severity.items()
            },
        },
    }


def _total(usages: Iterable[Usage]) -> Usage:
    acc = Usage()
    for usage in usages:
        acc = acc + usage
    return acc


def _jaccard(a: set[tuple[str, int | None]], b: set[tuple[str, int | None]]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _mean_of(values: list[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None


def _usage_payload(total: Usage, spread: Distribution | None) -> dict[str, object]:
    return {
        "prompt_tokens": total.prompt_tokens,
        "completion_tokens": total.completion_tokens,
        "cached_tokens": total.cached_tokens,
        "per_review": _distribution_payload(spread, digits=0),
    }


def _distribution_payload(spread: Distribution | None, *, digits: int) -> dict[str, float] | None:
    if spread is None:
        return None

    def shorten(value: float) -> float:
        # `digits=0` means whole tokens, and round() without ndigits gives an int
        return round(value, digits) if digits else round(value)

    return {
        "mean": shorten(spread.mean),
        "p50": shorten(spread.p50),
        "p90": shorten(spread.p90),
        "max": shorten(spread.max),
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
