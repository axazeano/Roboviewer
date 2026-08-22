"""The scope gate: is a finding about the change, or about the code around it?

Reviewers are handed changed files in full, which is what stops them reporting
handling that already exists twenty lines up. The cost is a standing temptation
to review the untouched 98% of the file, and on a real MR it dominated the
report: eleven of fifteen confirmed findings sat on lines the branch never
touched, and the author read the result as noise.

The answer is line arithmetic over the same map the markup was rendered from, so
it holds in any language and cannot disagree with what the agent was shown.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from roboviewer.models import Finding, Usage
from roboviewer.provider import AgentOutcome
from roboviewer.repo.diff import FileChanges
from roboviewer.review.checklist import ChecklistItem
from roboviewer.review.pipeline import ReviewPipeline
from roboviewer.review.scope import in_scope

from .conftest import ScriptedRunner, make_bundle, ok_outcome

ITEM = ChecklistItem(id="correctness", title="Correctness", body="Find logic errors.")

# One file: lines 40-42 added, and a block deleted from where line 100 now sits
CHANGES = {
    "src/cart.py": FileChanges(added={40, 41, 42}, removed_before={100: ["  old_total = 0"]})
}


def _at(line: int | None, file: str = "src/cart.py") -> bool:
    return in_scope(CHANGES, file, line, margin=5)


# ------------------------------------------------------------------ the rule


def test_a_changed_line_is_in_scope() -> None:
    assert _at(41)


def test_the_margin_reaches_the_declaration_just_above_an_edit() -> None:
    """A finding names the `func` line, the edit is inside the body. Same change."""
    assert _at(37) and _at(45)
    assert not _at(34) and not _at(48)


def test_a_deletion_anchors_scope_too() -> None:
    """Removing a method from a protocol is a change, and the only line left to
    point at is the neighbour that survived it."""
    assert _at(100) and _at(97)
    assert not _at(120)


def test_a_file_the_mr_never_touched_is_out_of_scope() -> None:
    assert not _at(41, file="src/untouched.py")


def test_a_finding_about_the_file_as_a_whole_stays() -> None:
    """No line means the remark is about the file, and this file did change."""
    assert _at(None)


def test_a_leading_dot_slash_is_the_same_path() -> None:
    assert _at(41, file="./src/cart.py")


def test_without_a_map_nothing_is_dropped() -> None:
    """A caller that never built one gets no gate rather than an empty report."""
    assert in_scope({}, "src/cart.py", 900, margin=5)


# ------------------------------------------------------------------ in the pipeline


def _finding(line: int, title: str, file: str = "src/cart.py") -> dict:
    return Finding(
        file=file, line=line, title=title, rationale=f"rationale for {title}", confidence=0.9
    ).model_dump(mode="json")


def _run(config, tmp_path: Path, findings: list[dict]):
    config.run.enable_judge = True
    runner = ScriptedRunner(
        ok_outcome(findings=findings),
        AgentOutcome(
            payload={
                "summary": "checked",
                # Ids are assigned by the pipeline; the judge answers about F001
                "verdicts": [{"finding_id": "F001", "verdict": "confirmed", "reason": "checked"}],
            },
            usage=Usage(),
            turns=1,
        ),
    )
    bundle = make_bundle(tmp_path, lines=CHANGES)
    run = asyncio.run(ReviewPipeline(config, bundle, [ITEM], runner).execute())
    return run, runner


def test_findings_off_the_change_are_set_aside_not_reported(tmp_path: Path, config) -> None:
    run, _ = _run(
        config,
        tmp_path,
        [_finding(41, "Off-by-one in the new branch"), _finding(300, "Hardcoded string")],
    )

    assert [f.title for f in run.findings] == ["Off-by-one in the new branch"]
    assert [f.title for f in run.out_of_scope] == ["Hardcoded string"]
    # Set aside, not deleted: the author can still read them
    assert run.out_of_scope[0].id == "X001"
    assert run.findings[0].id == "F001"


def test_the_judge_is_not_paid_to_verify_them(tmp_path: Path, config) -> None:
    """A pass spent on code the MR did not touch buys a line the author has no
    reason to act on in this review."""
    _, runner = _run(
        config, tmp_path, [_finding(41, "Off-by-one"), _finding(300, "Hardcoded string")]
    )

    judging = [r for r in runner.requests if r.metadata.get("stage") == "judge"]
    assert len(judging) == 1
    assert "Hardcoded string" not in judging[0].prompt


def test_the_gate_can_be_switched_off(tmp_path: Path, config) -> None:
    config.run.enforce_scope = False
    run, _ = _run(config, tmp_path, [_finding(41, "Off-by-one"), _finding(300, "Hardcoded string")])
    assert len(run.findings) == 2
    assert run.out_of_scope == []


def test_a_wider_margin_lets_more_of_the_file_back_in(tmp_path: Path, config) -> None:
    config.run.scope_margin = 40
    run, _ = _run(config, tmp_path, [_finding(41, "Off-by-one"), _finding(70, "Nearby remark")])
    assert len(run.findings) == 2


def test_the_report_lists_what_the_gate_set_aside(tmp_path: Path, config) -> None:
    from roboviewer.reports.renders.markdown import render as render_md

    run, _ = _run(config, tmp_path, [_finding(41, "Off-by-one"), _finding(300, "Hardcoded string")])
    text = render_md(run)

    assert "Outside the changed lines (1)" in text
    assert "Hardcoded string" in text
