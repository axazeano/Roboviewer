"""Collapsing what several reviewers said about the same line.

Eight agents read the same files, so the same problem arrives up to eight times
in different words. Merging is deliberately timid — showing one finding twice
costs a line of the report, losing a real one costs the review — and these tests
pin where that timidity sits: what counts as the same place, what counts as the
same wording, and which of two versions survives.
"""

from __future__ import annotations

from roboviewer.models import Finding, ItemResult, Severity
from roboviewer.pipeline import merge_findings


def _finding(**overrides: object) -> Finding:
    fields: dict[str, object] = {
        "file": "src/cart.py",
        "line": 42,
        "severity": Severity.MAJOR,
        "category": "logic",
        "title": "Discount is applied twice",
        "rationale": "apply() adds the discount and then recomputes the total.",
        "confidence": 0.8,
    }
    fields.update(overrides)
    return Finding(**fields)  # type: ignore[arg-type]


def _item(item_id: str, *findings: Finding) -> ItemResult:
    return ItemResult(item_id=item_id, item_title=item_id, findings=list(findings))


# ------------------------------------------------------------------ what merges


def test_the_same_problem_from_two_reviewers_becomes_one() -> None:
    merged = merge_findings(
        [
            _item("correctness", _finding()),
            _item("tests", _finding(title="The discount is applied twice")),
        ]
    )

    assert len(merged) == 1
    assert merged[0].sources == ["correctness", "tests"]


def test_a_matching_rationale_is_enough_on_its_own() -> None:
    """Two agents can name a problem differently and still describe the same
    thing; the rationale is the longer text and the safer signal."""
    merged = merge_findings(
        [
            _item("correctness", _finding(title="Double discount")),
            _item("api", _finding(title="Total is wrong for coupon codes")),
        ]
    )

    assert len(merged) == 1


def test_neighbouring_lines_merge_within_the_window() -> None:
    merged = merge_findings(
        [_item("correctness", _finding(line=41)), _item("tests", _finding(line=43))]
    )

    assert len(merged) == 1


# ------------------------------------------------------------------ what does not


def test_the_window_is_arithmetic_so_a_boundary_can_split_neighbours() -> None:
    """The bucket is (line - 1) // 5, so lines 5 and 6 land either side of it.
    Timid on purpose: the pair survives as two findings rather than one."""
    merged = merge_findings(
        [_item("correctness", _finding(line=5)), _item("tests", _finding(line=6))]
    )

    assert len(merged) == 2


def test_the_same_wording_in_another_file_stays_separate() -> None:
    merged = merge_findings(
        [_item("correctness", _finding()), _item("tests", _finding(file="src/order.py"))]
    )

    assert len(merged) == 2


def test_two_different_problems_on_one_line_both_survive() -> None:
    merged = merge_findings(
        [
            _item("correctness", _finding()),
            _item("security", _finding(
                title="Coupon code is interpolated into SQL",
                rationale="The value reaches the query without binding.",
            )),
        ]
    )

    assert len(merged) == 2


# ------------------------------------------------------------------ which one wins


def test_the_heavier_severity_survives_and_keeps_both_sources() -> None:
    merged = merge_findings(
        [
            _item("tests", _finding(severity=Severity.MINOR, title="Discount looks off")),
            _item("correctness", _finding(severity=Severity.BLOCKER)),
        ]
    )

    assert len(merged) == 1
    assert merged[0].severity is Severity.BLOCKER
    assert merged[0].sources == ["correctness", "tests"]


def test_at_equal_severity_the_more_confident_wording_survives() -> None:
    merged = merge_findings(
        [
            _item("tests", _finding(confidence=0.4, title="Discount may be applied twice")),
            _item("correctness", _finding(confidence=0.95, title="Discount is applied twice")),
        ]
    )

    assert merged[0].confidence == 0.95
    assert merged[0].title == "Discount is applied twice"


# ------------------------------------------------------------------ the list itself


def test_findings_below_min_confidence_never_reach_the_judge() -> None:
    merged = merge_findings(
        [_item("correctness", _finding(confidence=0.2, line=99))], min_confidence=0.5
    )

    assert merged == []


def test_the_list_is_ordered_by_severity_then_confidence_and_numbered() -> None:
    merged = merge_findings(
        [
            _item("a", _finding(line=10, severity=Severity.MINOR, title="Minor one")),
            _item("b", _finding(line=20, severity=Severity.BLOCKER, title="Blocker")),
            _item("c", _finding(line=30, severity=Severity.MINOR, confidence=0.9,
                                title="Minor two")),
        ]
    )

    assert [f.severity for f in merged] == [Severity.BLOCKER, Severity.MINOR, Severity.MINOR]
    assert [f.confidence for f in merged] == [0.8, 0.9, 0.8]
    assert [f.id for f in merged] == ["F001", "F002", "F003"]


def test_a_finding_about_a_whole_file_has_its_own_bucket() -> None:
    """No line means no window to fall into, so file-level findings only ever
    merge with other file-level findings about the same file."""
    merged = merge_findings(
        [_item("architecture", _finding(line=None)), _item("correctness", _finding())]
    )

    assert len(merged) == 2
