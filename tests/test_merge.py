"""Collapsing what several reviewers said about the same line.

Eight agents read the same files, so the same problem arrives up to eight times
in different words. What separates one defect written up twice from two defects
on one line is the code each names, not how alike they read: these tests pin
that, the window around a line, and which of two versions survives.
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


def test_adjacent_lines_merge_wherever_they_fall() -> None:
    """The window used to be `(line - 1) // 5`, a grid rather than a window, so
    lines 5 and 6 sat either side of a boundary and were never compared. Every
    pair one line apart is now within it."""
    merged = merge_findings(
        [_item("correctness", _finding(line=5)), _item("tests", _finding(line=6))]
    )

    assert len(merged) == 1


def test_the_same_symbols_merge_however_unlike_the_wording() -> None:
    """Two agents on one defect, from redis/redis#13959: the titles share
    almost no characters, and both name `exprPrintToken` and `EXPR_TOKEN_NULL`."""
    merged = merge_findings(
        [
            _item("tests", _finding(
                line=871,
                title="exprPrintToken() missing case for EXPR_TOKEN_NULL",
                rationale="The new EXPR_TOKEN_NULL token type is not handled in exprPrintToken().",
            )),
            _item("api-contracts", _finding(
                line=872,
                title="EXPR_TOKEN_NULL not handled in exprPrintToken() switch",
                rationale="The exprPrintToken() function switches on token_type with no case "
                          "for EXPR_TOKEN_NULL.",
            )),
        ]
    )

    assert len(merged) == 1
    assert merged[0].sources == ["api-contracts", "tests"]


# ------------------------------------------------------------------ what does not


def test_one_line_two_defects_survive_alike_wording() -> None:
    """Also from #13959, on one line of expr.c: a missing test and a missing
    word-boundary check. The titles read alike, the code they name does not."""
    merged = merge_findings(
        [
            _item("tests", _finding(
                line=263,
                title="null literal parsing has no dedicated test",
                rationale="The new null literal in exprParseOperatorOrLiteral() and the "
                          "EXPR_TOKEN_NULL handling in exprTokenToBool() are never exercised.",
            )),
            _item("correctness", _finding(
                line=263,
                title="Null literal parsing doesn't verify word boundary",
                rationale="The check matches 'null' when matchlen == 4, but the loop above "
                          "consumes every alphabetic character, so 'nullx' matches too.",
            )),
        ]
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
