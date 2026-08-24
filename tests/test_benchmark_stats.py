"""Statistics over a benchmark's reviews.

What is pinned: percentiles are nearest-rank, token totals split runner from
judge, and self-consistency is mean pairwise Jaccard over confirmed findings
keyed by file and line — so a repeat that words a title differently still
counts as the same finding, and a rejected one does not count at all.
"""

from __future__ import annotations

import pytest

from roboviewer.benchmark import stats
from roboviewer.models import Finding, ItemResult, ReviewRun, Severity, Usage, Verdict


def finding(
    id: str, file: str = "cart.py", line: int = 1, severity: Severity = Severity.MINOR
) -> Finding:
    return Finding(id=id, file=file, line=line, severity=severity, title=id, rationale=".")


def run_with(
    *findings: Finding,
    verdicts: dict[str, Verdict] | None = None,
    runner: Usage | None = None,
    judge: Usage | None = None,
) -> ReviewRun:
    return ReviewRun(
        run_id="run-1",
        repo_root=".",
        branch="feature",
        target="trunk",
        base_sha="base",
        head_sha="head",
        model="stub",
        started_at="2026-08-23T10:00:00Z",
        items=[ItemResult(item_id="i1", item_title="item", usage=runner or Usage())],
        findings=list(findings),
        verdicts=verdicts or {},
        judge_usage=judge or Usage(),
    )


def review(run: ReviewRun, seconds: float = 10.0) -> stats.Review:
    return stats.Review(run=run, seconds=seconds)


# ------------------------------------------------------------------ spreads


def test_percentiles_are_nearest_rank() -> None:
    values = [4.0, 1.0, 3.0, 2.0]
    assert stats.percentile(values, 50) == 2.0
    assert stats.percentile(values, 90) == 4.0
    assert stats.percentile([7.0], 50) == 7.0
    with pytest.raises(ValueError, match="empty"):
        stats.percentile([], 50)


def test_a_distribution_over_nothing_is_none() -> None:
    assert stats.Distribution.over([]) is None
    spread = stats.Distribution.over([10.0, 20.0])
    assert spread is not None
    assert (spread.mean, spread.p50, spread.max) == (15.0, 10.0, 20.0)


# ------------------------------------------------------------------ consistency


def test_identical_repeats_agree_completely() -> None:
    twice = [run_with(finding("f1"), finding("f2", line=9)) for _ in range(2)]
    assert stats.consistency(twice) == 1.0


def test_wording_does_not_matter_where_the_location_does() -> None:
    def worded(id: str, title: str) -> Finding:
        return Finding(id=id, file="cart.py", line=3, title=title, rationale=".")

    runs = [run_with(worded("a", "Drops a line")), run_with(worded("b", "A line is lost"))]
    assert stats.consistency(runs) == 1.0


def test_disjoint_repeats_do_not_agree() -> None:
    runs = [run_with(finding("f1", line=1)), run_with(finding("f2", line=2))]
    assert stats.consistency(runs) == 0.0


def test_partial_overlap_is_the_mean_pairwise_jaccard() -> None:
    shared = finding("shared", line=1)
    runs = [run_with(shared), run_with(shared, finding("extra", line=2))]
    assert stats.consistency(runs) == 0.5


def test_repeats_that_both_found_nothing_agree() -> None:
    assert stats.consistency([run_with(), run_with()]) == 1.0


def test_one_review_has_nothing_to_compare() -> None:
    assert stats.consistency([run_with(finding("f1"))]) is None


def test_a_rejected_finding_does_not_count() -> None:
    doubted = run_with(
        finding("f1", line=1),
        finding("f2", line=2),
        verdicts={"f2": Verdict(finding_id="f2", verdict="false_positive")},
    )
    confident = run_with(finding("f3", line=1))
    assert stats.consistency([doubted, confident]) == 1.0


def test_consistency_is_sliced_by_severity() -> None:
    first = run_with(
        finding("b1", line=1, severity=Severity.BLOCKER),
        finding("m1", line=2, severity=Severity.MINOR),
    )
    second = run_with(
        finding("b2", line=1, severity=Severity.BLOCKER),
        finding("m2", line=3, severity=Severity.MINOR),
    )
    assert stats.consistency([first, second], Severity.BLOCKER) == 1.0
    assert stats.consistency([first, second], Severity.MINOR) == 0.0
    assert stats.consistency([first, second]) == pytest.approx(1 / 3)


def test_a_severity_nobody_found_is_not_scored() -> None:
    runs = [run_with(finding("f1")), run_with(finding("f2"))]
    assert stats.consistency(runs, Severity.BLOCKER) is None


# ------------------------------------------------------------------ groups


def test_a_group_splits_runner_from_judge_and_totals_the_repeats() -> None:
    first = review(
        run_with(
            runner=Usage(prompt_tokens=100, completion_tokens=10, cached_tokens=40),
            judge=Usage(prompt_tokens=50, completion_tokens=5, cached_tokens=20),
        ),
        seconds=10.0,
    )
    second = review(
        run_with(
            runner=Usage(prompt_tokens=200, completion_tokens=20, cached_tokens=80),
            judge=Usage(prompt_tokens=70, completion_tokens=7, cached_tokens=30),
        ),
        seconds=30.0,
    )

    group = stats.group("cli-13946", [first, second])

    assert group.reviews == 2
    assert (group.runner.prompt_tokens, group.runner.completion_tokens) == (300, 30)
    assert group.runner.cached_tokens == 120
    assert (group.judge.prompt_tokens, group.judge.completion_tokens) == (120, 12)
    assert group.judge.cached_tokens == 50
    assert group.runner_tokens is not None
    assert (group.runner_tokens.mean, group.runner_tokens.max) == (165.0, 220.0)
    assert group.judge_tokens is not None
    assert group.judge_tokens.mean == 66.0
    assert group.seconds is not None
    assert (group.seconds.mean, group.seconds.p50, group.seconds.max) == (20.0, 10.0, 30.0)


def test_the_whole_run_pools_tokens_and_averages_consistency_over_entries() -> None:
    agreeing = [review(run_with(finding("f1"))), review(run_with(finding("f2")))]
    disagreeing = [
        review(run_with(finding("g1", line=1))),
        review(run_with(finding("g2", line=2))),
    ]
    lone = [review(run_with(finding("h1")))]
    groups = [
        stats.group("one", agreeing),
        stats.group("two", disagreeing),
        stats.group("three", lone),
    ]

    whole = stats.whole("Whole run", groups, agreeing + disagreeing + lone)

    assert whole.reviews == 5
    # 1.0 and 0.0 averaged; the lone review has no consistency and stays out
    assert whole.consistency == 0.5


def test_a_run_with_no_reviews_has_totals_and_no_spreads() -> None:
    whole = stats.whole("Whole run", [], [])
    assert whole.reviews == 0
    assert whole.runner.total_tokens == 0
    assert whole.runner_tokens is None
    assert whole.consistency is None


def test_the_payload_carries_what_the_summary_json_promises() -> None:
    group = stats.group("cli-13946", [review(run_with(finding("f1"))) for _ in range(2)])

    payload = stats.payload(group)

    assert payload["title"] == "cli-13946"
    assert payload["reviews"] == 2
    runner = payload["runner"]
    assert isinstance(runner, dict)
    assert set(runner) == {"prompt_tokens", "completion_tokens", "cached_tokens", "per_review"}
    consistency = payload["consistency"]
    assert isinstance(consistency, dict)
    assert consistency["all"] == 1.0
    assert consistency["by_severity"] == {
        "blocker": None,
        "major": None,
        "minor": 1.0,
        "nit": None,
    }
