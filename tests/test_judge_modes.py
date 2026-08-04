"""Judging modes: one pass over the whole list, or a pass per finding and then one.

The two-stage mode exists because a single batch pass spreads its turn budget
across every claim and confirms what merely reads as plausible. Its first stage
is what these tests mostly pin: the verdict lands on the finding the pass was
actually given, one failure costs one verdict, and a `duplicate` verdict
survives the loss of the batch judge's view of the list.

The second stage buys that view back. Its tests pin what it may and may not
touch: it sees the survivors and their verification notes, it recalibrates
severity, and it cannot resurrect a finding the verification already rejected.

Where a test is about the first stage alone, the ruling is scripted to change
nothing — the stage is not optional, so it has to be there and stay out of the
way.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from roboviewer.checklist import ChecklistItem
from roboviewer.config import RunConfig
from roboviewer.models import Finding, Severity, Usage
from roboviewer.pipeline import ReviewPipeline
from roboviewer.runners import AgentOutcome, AgentRequest, ProgressHook, Runner
from roboviewer.tools import SUBMIT_VERDICT_TOOL, SUBMIT_VERDICTS_TOOL

from .conftest import ScriptedRunner, make_bundle, ok_outcome

ITEM = ChecklistItem(id="correctness", title="Correctness", body="Find logic errors.")


def _finding(line: int, title: str, severity: Severity = Severity.MAJOR) -> Finding:
    # Distinct titles and far-apart lines: merge_findings collapses neighbours,
    # and these have to survive it as separate findings.
    return Finding(
        file="src/cart.py",
        line=line,
        severity=severity,
        category="logic",
        title=title,
        rationale=f"rationale for {title}",
        confidence=0.9,
    )


class VerdictRunner(Runner):
    """Answers each judging pass according to the finding it was handed.

    Keyed on request metadata rather than call order: the passes fan out
    concurrently, so a positional script would be testing the scheduler.
    """

    name = "verdicts"

    def __init__(
        self,
        item_outcome: AgentOutcome,
        verdicts: dict[str, Any],
        final: AgentOutcome | None = None,
    ) -> None:
        self._item_outcome = item_outcome
        self._verdicts = verdicts
        self._final = final
        self.requests: list[AgentRequest] = []

    async def run(
        self,
        request: AgentRequest,
        on_progress: ProgressHook | None = None,  # noqa: ARG002 — the Runner signature
    ) -> AgentOutcome:
        self.requests.append(request)
        if request.metadata.get("pass") == "final":
            assert self._final is not None, "the run reached a final pass the test did not script"
            return self._final
        finding_id = request.metadata.get("finding_id")
        if finding_id is None:
            return self._item_outcome
        answer = self._verdicts[finding_id]
        if isinstance(answer, AgentOutcome):
            return answer
        return AgentOutcome(payload=answer, usage=Usage(completion_tokens=10), turns=2)

    def judging(self) -> list[AgentRequest]:
        return [r for r in self.requests if r.metadata.get("stage") == "judge"]

    def verifications(self) -> list[AgentRequest]:
        """The first stage only: one request per finding."""
        return [r for r in self.requests if r.metadata.get("finding_id")]

    def final(self) -> AgentRequest | None:
        return next((r for r in self.requests if r.metadata.get("pass") == "final"), None)


# A second stage that rules on nothing, for the tests that are about the first
NO_RULING = AgentOutcome(payload={"summary": "", "verdicts": []}, usage=Usage(), turns=1)


def _run(
    config,
    tmp_path: Path,
    findings: list[Finding],
    verdicts: dict[str, Any],
    *,
    mode: str = "two_stage",
    final: AgentOutcome | None = None,
) -> tuple[Any, VerdictRunner]:
    config.run.enable_judge = True
    config.run.judge_mode = mode
    runner = VerdictRunner(
        ok_outcome(findings=[f.model_dump(mode="json") for f in findings]),
        verdicts,
        final if final is not None else NO_RULING,
    )
    run = asyncio.run(ReviewPipeline(config, make_bundle(tmp_path), [ITEM], runner).execute())
    return run, runner



def _ruling(summary: str, verdicts: list[dict[str, Any]]) -> AgentOutcome:
    return AgentOutcome(
        payload={"summary": summary, "verdicts": verdicts},
        usage=Usage(completion_tokens=7),
        turns=3,
    )


# ------------------------------------------------------------------ turn budget


def test_judging_turns_follow_max_turns_until_set() -> None:
    assert RunConfig(max_turns=25).resolve_judge_max_turns() == 25
    assert RunConfig(max_turns=25, judge_max_turns=8).resolve_judge_max_turns() == 8


# ------------------------------------------------------------------ fan-out


def test_one_pass_per_finding_each_seeing_only_its_own(tmp_path: Path, config) -> None:
    findings = [_finding(42, "Off-by-one"), _finding(120, "Unclosed file")]
    run, runner = _run(
        config,
        tmp_path,
        findings,
        {
            "F001": {"verdict": "confirmed", "reason": "checked src/cart.py:42"},
            "F002": {
                "verdict": "false_positive",
                "reason": "the file is closed by the context manager",
            },
        },
    )

    verifications = runner.verifications()
    assert len(verifications) == 2
    assert all(r.terminal_tool is SUBMIT_VERDICT_TOOL for r in verifications)

    # Each pass carries the claim it is meant to settle, and not the other one
    by_id = {r.metadata["finding_id"]: r.prompt for r in verifications}
    assert "Off-by-one" in by_id["F001"] and "rationale for Off-by-one" in by_id["F001"]
    assert "rationale for Unclosed file" not in by_id["F001"]

    assert run.verdicts["F001"].verdict == "confirmed"
    assert run.verdicts["F002"].verdict == "false_positive"
    assert [f.id for f in run.confirmed()] == ["F001"]


def test_the_verdict_belongs_to_the_finding_the_pass_was_given(tmp_path: Path, config) -> None:
    """A pass sees one finding, so an id echoed back by the model is only an
    opportunity to get it wrong. Ours wins."""
    findings = [_finding(42, "Off-by-one"), _finding(120, "Unclosed file")]
    run, _ = _run(
        config,
        tmp_path,
        findings,
        {
            "F001": {"finding_id": "F999", "verdict": "confirmed", "reason": "ok"},
            "F002": {"finding_id": "F001", "verdict": "confirmed", "reason": "ok"},
        },
    )

    assert set(run.verdicts) == {"F001", "F002"}
    assert run.verdicts["F001"].finding_id == "F001"
    assert run.verdicts["F002"].finding_id == "F002"


def test_the_roster_lists_the_others_so_duplicates_stay_findable(tmp_path: Path, config) -> None:
    findings = [_finding(42, "Off-by-one"), _finding(120, "Unclosed file")]
    _, runner = _run(
        config,
        tmp_path,
        findings,
        {
            "F001": {"verdict": "confirmed", "reason": "ok"},
            "F002": {"verdict": "duplicate", "reason": "same as F001"},
        },
    )

    by_id = {r.metadata["finding_id"]: r.prompt for r in runner.verifications()}
    assert "F002" in by_id["F001"] and "Unclosed file" in by_id["F001"]
    # The roster names the others; the finding under test is above it, in full
    assert "- F001" not in by_id["F001"]


def test_a_single_finding_gets_no_roster(tmp_path: Path, config) -> None:
    _, runner = _run(
        config, tmp_path, [_finding(42, "Off-by-one")],
        {"F001": {"verdict": "confirmed", "reason": "ok"}},
    )
    assert "The other findings" not in runner.verifications()[0].prompt


# ------------------------------------------------------------------ resilience


def test_one_failed_pass_costs_one_verdict(tmp_path: Path, config) -> None:
    """The batch judge is all-or-nothing: if it dies, no finding is judged. Here
    the blast radius is a single finding, and it still reaches the author."""
    findings = [_finding(42, "Off-by-one"), _finding(120, "Unclosed file")]
    run, _ = _run(
        config,
        tmp_path,
        findings,
        {
            "F001": AgentOutcome(payload=None, error="gateway timeout", usage=Usage(), turns=3),
            "F002": {"verdict": "false_positive", "reason": "already handled"},
        },
    )

    assert run.verdicts["F001"].verdict == "unreviewed"
    assert "gateway timeout" in run.verdicts["F001"].reason
    assert run.verdicts["F002"].verdict == "false_positive"
    # Unjudged is not rejected — it still shows up for the author
    assert [f.id for f in run.confirmed()] == ["F001"]


def test_a_malformed_verdict_leaves_the_finding_unjudged(tmp_path: Path, config) -> None:
    run, _ = _run(
        config, tmp_path, [_finding(42, "Off-by-one")],
        {"F001": {"verdict": "probably fine", "reason": "hmm"}},
    )
    assert run.verdicts["F001"].verdict == "unreviewed"
    assert "malformed" in run.verdicts["F001"].reason


# ------------------------------------------------------------------ bookkeeping


def test_severity_corrections_apply_and_reorder(tmp_path: Path, config) -> None:
    findings = [_finding(42, "Off-by-one", Severity.MAJOR), _finding(120, "Typo", Severity.MINOR)]
    run, _ = _run(
        config,
        tmp_path,
        findings,
        {
            "F001": {"verdict": "nitpick", "severity": "nit", "reason": "cosmetic"},
            "F002": {"verdict": "confirmed", "severity": "blocker", "reason": "data loss"},
        },
    )

    by_id = {f.id: f for f in run.findings}
    assert by_id["F001"].severity is Severity.NIT
    assert by_id["F002"].severity is Severity.BLOCKER
    # Sorted by the corrected severity, not the one the reviewer claimed
    assert [f.id for f in run.findings] == ["F002", "F001"]


def test_usage_is_summed_across_passes(tmp_path: Path, config) -> None:
    findings = [_finding(42, "Off-by-one"), _finding(120, "Unclosed file")]
    run, _ = _run(
        config,
        tmp_path,
        findings,
        {
            "F001": {"verdict": "confirmed", "reason": "ok"},
            "F002": {"verdict": "confirmed", "reason": "ok"},
        },
    )
    assert run.judge_usage.completion_tokens == 20


def test_the_summary_reports_the_tally(tmp_path: Path, config) -> None:
    """A per-finding judge never sees the whole picture, so the report gets
    counts rather than prose — and empty buckets stay out of it."""
    findings = [_finding(42, "Off-by-one"), _finding(120, "Unclosed file")]
    run, _ = _run(
        config,
        tmp_path,
        findings,
        {
            "F001": {"verdict": "confirmed", "reason": "ok"},
            "F002": {"verdict": "false_positive", "reason": "no"},
        },
    )
    assert "1 confirmed" in run.judge_summary
    assert "1 rejected as false positives" in run.judge_summary
    assert "duplicate" not in run.judge_summary


# ------------------------------------------------------------------ two stages


def _three(config, tmp_path: Path, final: AgentOutcome) -> tuple[Any, VerdictRunner]:
    """Three findings, the third rejected by its own pass — so every two-stage
    test has both a survivor set and something that must stay out of it."""
    findings = [
        _finding(42, "Off-by-one"),
        _finding(120, "Unclosed file"),
        _finding(200, "Missing tests", Severity.MAJOR),
    ]
    return _run(
        config,
        tmp_path,
        findings,
        {
            "F001": {"verdict": "confirmed", "reason": "grep for close() returns nothing"},
            "F002": {"verdict": "confirmed", "reason": "read src/cart.py:120, no context manager"},
            "F003": {"verdict": "false_positive", "reason": "tests/test_cart.py covers it"},
        },
        mode="two_stage",
        final=final,
    )


def test_two_stage_rules_only_on_what_survived_verification(tmp_path: Path, config) -> None:
    run, runner = _three(
        config,
        tmp_path,
        _ruling(
            "A small MR with one real bug.",
            [
                {"finding_id": "F001", "verdict": "confirmed", "reason": "stands"},
                {"finding_id": "F002", "verdict": "nitpick", "severity": "nit",
                 "reason": "cosmetic"},
            ],
        ),
    )

    # Three verification passes, then one ruling over the two that survived
    assert len(runner.judging()) == 4
    prompt = runner.final().prompt
    assert "Off-by-one" in prompt and "Unclosed file" in prompt
    assert "Missing tests" not in prompt
    assert runner.final().terminal_tool is SUBMIT_VERDICTS_TOOL

    assert run.verdicts["F002"].verdict == "nitpick"
    assert run.verdicts["F003"].verdict == "false_positive"


def test_the_final_pass_reads_what_each_verification_found(tmp_path: Path, config) -> None:
    """The note is the point of running verification first: without it the second
    pass either trusts the claim or pays for the same searches again."""
    _, runner = _three(
        config,
        tmp_path,
        _ruling("ok", [{"finding_id": "F001", "verdict": "confirmed", "reason": "stands"}]),
    )

    prompt = runner.final().prompt
    assert "Verified: grep for close() returns nothing" in prompt
    assert "read src/cart.py:120, no context manager" in prompt


def test_the_final_pass_recalibrates_severity_across_findings(tmp_path: Path, config) -> None:
    """What a per-finding pass cannot do: it holds one claim and has nothing to
    weigh it against."""
    run, _ = _three(
        config,
        tmp_path,
        _ruling(
            "One blocker, one nit.",
            [
                {"finding_id": "F001", "verdict": "confirmed", "severity": "nit",
                 "reason": "trivial"},
                {"finding_id": "F002", "verdict": "confirmed", "severity": "blocker",
                 "reason": "data loss"},
            ],
        ),
    )

    by_id = {f.id: f for f in run.findings}
    assert by_id["F001"].severity is Severity.NIT
    assert by_id["F002"].severity is Severity.BLOCKER
    assert [f.id for f in run.confirmed()] == ["F002", "F001"]


def test_the_final_pass_cannot_resurrect_a_rejected_finding(tmp_path: Path, config) -> None:
    """It never sees the rejects, and a verdict naming one is ignored rather than
    applied: verification spent a whole turn budget killing that claim."""
    run, _ = _three(
        config,
        tmp_path,
        _ruling(
            "ok",
            [
                {"finding_id": "F001", "verdict": "confirmed", "reason": "stands"},
                {"finding_id": "F003", "verdict": "confirmed", "severity": "blocker",
                 "reason": "looks real"},
            ],
        ),
    )

    assert run.verdicts["F003"].verdict == "false_positive"
    assert {f.id for f in run.confirmed()} == {"F001", "F002"}


def test_agreeing_keeps_the_reason_that_says_what_was_checked(tmp_path: Path, config) -> None:
    """A ruling that agrees adds nothing to the record; the check that named a
    file and a line does."""
    run, _ = _three(
        config,
        tmp_path,
        _ruling(
            "ok",
            [
                {"finding_id": "F001", "verdict": "confirmed", "reason": "agreed"},
                {"finding_id": "F002", "verdict": "nitpick", "reason": "too small to report"},
            ],
        ),
    )

    assert run.verdicts["F001"].reason == "grep for close() returns nothing"
    # A changed verdict has to explain itself, so that reason wins
    assert run.verdicts["F002"].reason == "too small to report"


def test_a_recalibrated_finding_carries_the_reason_that_recalibrated_it(
    tmp_path: Path, config
) -> None:
    """Seen in a real run: the individual pass wrote "severity lowered from major
    to minor", the final pass moved it to nit, and keeping the earlier text put a
    sentence about minor next to a Nit badge."""
    run, _ = _three(
        config,
        tmp_path,
        _ruling(
            "ok",
            [{"finding_id": "F001", "verdict": "confirmed", "severity": "nit",
              "reason": "dead code, not a bug"}],
        ),
    )

    assert run.findings[-1].id == "F001"
    assert run.verdicts["F001"].reason == "dead code, not a bug"
    assert run.verdicts["F001"].severity is Severity.NIT


def test_a_finding_the_final_pass_skips_keeps_its_own_verdict(tmp_path: Path, config) -> None:
    run, _ = _three(
        config,
        tmp_path,
        _ruling("ok", [{"finding_id": "F001", "verdict": "confirmed", "reason": "stands"}]),
    )
    assert run.verdicts["F002"].verdict == "confirmed"
    assert run.verdicts["F002"].reason == "read src/cart.py:120, no context manager"


def test_a_failed_final_pass_costs_calibration_not_the_verdicts(tmp_path: Path, config) -> None:
    run, _ = _three(
        config,
        tmp_path,
        AgentOutcome(payload=None, error="gateway timeout", usage=Usage(), turns=1),
    )

    assert {f.id for f in run.confirmed()} == {"F001", "F002"}
    assert run.verdicts["F001"].reason == "grep for close() returns nothing"
    assert "gateway timeout" in run.judge_summary


def test_the_two_stage_summary_counts_after_both_stages(tmp_path: Path, config) -> None:
    run, _ = _three(
        config,
        tmp_path,
        _ruling(
            "Refactoring, safe to merge once F001 is fixed.",
            [
                {"finding_id": "F001", "verdict": "confirmed", "reason": "stands"},
                {"finding_id": "F002", "verdict": "duplicate", "reason": "same as F001"},
            ],
        ),
    )

    # 1 confirmed, 1 duplicate, 1 false positive — the second stage's verdict is
    # in the count, not the intermediate state where two were confirmed
    assert "1 confirmed" in run.judge_summary
    assert "1 rejected as duplicates" in run.judge_summary
    assert "1 rejected as false positives" in run.judge_summary
    assert "Refactoring, safe to merge once F001 is fixed." in run.judge_summary


def test_nothing_survives_so_no_ruling_is_paid_for(tmp_path: Path, config) -> None:
    """The final outcome is deliberately absent: reaching it would fail the run."""
    run, runner = _run(
        config,
        tmp_path,
        [_finding(42, "Off-by-one")],
        {"F001": {"verdict": "false_positive", "reason": "already handled"}},
        mode="two_stage",
    )
    assert runner.final() is None
    assert run.judge_summary.endswith("1 rejected as false positives.")


def test_two_stage_usage_covers_both_stages(tmp_path: Path, config) -> None:
    run, _ = _three(
        config,
        tmp_path,
        _ruling("ok", [{"finding_id": "F001", "verdict": "confirmed", "reason": "stands"}]),
    )
    assert run.judge_usage.completion_tokens == 3 * 10 + 7


# ------------------------------------------------------------------ the default


def test_batch_stays_the_default_and_judges_in_one_pass(tmp_path: Path, config) -> None:
    assert RunConfig().judge_mode == "batch"

    config.run.enable_judge = True
    finding = _finding(42, "Off-by-one")
    runner = ScriptedRunner(
        ok_outcome(findings=[finding.model_dump(mode="json")]),
        AgentOutcome(
            payload={
                "summary": "One real problem.",
                "verdicts": [{"finding_id": "F001", "verdict": "confirmed", "reason": "checked"}],
            },
            usage=Usage(),
            turns=1,
        ),
    )
    run = asyncio.run(ReviewPipeline(config, make_bundle(tmp_path), [ITEM], runner).execute())

    judging = [r for r in runner.requests if r.metadata.get("stage") == "judge"]
    assert len(judging) == 1
    assert judging[0].terminal_tool is SUBMIT_VERDICTS_TOOL
    assert run.judge_summary == "One real problem."
    assert run.verdicts["F001"].verdict == "confirmed"
