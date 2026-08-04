"""Verifying findings: what survives, at what severity, and why.

The reviewers report; this stage decides what reaches the author. Two ways of
doing it, and what separates them is how much of the list one pass holds at once.

`batch` is a single pass over every finding. It sees the whole list, so
duplicates and relative severity come easily to it — at the price of spreading
one turn budget across every claim, and of losing every verdict if it dies.

`two_stage` verifies each finding on its own first: a full turn budget for a
single claim, and a failure that costs one verdict instead of all of them. Then
one pass rules on what survived, which is the part no per-finding pass can do —
holding one claim, it has no scale to weigh it against, so severities drift up
and the same complaint confirmed once per file arrives five times. Measured
against the batch judge on a 64-file MR: seven duplicates collapsed where the
text-similarity merge caught none, and the tail moved out of Minor into Nit.

The two are separate classes rather than two branches in one: they share how a
pass reaches the model, which is `Passes`, and nothing else. Adding a third way
means adding a class, not editing the one that picks.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .events import Event, EventSink
from .gitdiff import DiffBundle
from .models import Finding, ReviewRun, Severity, Usage, Verdict
from .prompts import Prompts
from .runners import AgentOutcome, AgentRequest, Runner
from .tools import SUBMIT_VERDICT_TOOL, SUBMIT_VERDICTS_TOOL


class Judge(Protocol):
    """What a run needs from its judging stage.

    `rule` fills in `run.verdicts`, `run.judge_summary` and any corrected
    severity. It answers in the run rather than in a return value because a
    verdict is only meaningful next to the finding it belongs to.
    """

    async def rule(self, run: ReviewRun) -> None: ...


@dataclass(frozen=True)
class JudgeSettings:
    """The five values judging takes out of the config.

    Named apart so a judging pass can be built and tested without a whole
    `Config`, and so it is visible at a glance that the judge may run on a
    different model, budget and reasoning mode than the reviewers.
    """

    mode: str
    model: str
    max_turns: int
    enable_thinking: bool | None
    concurrency: int


@dataclass
class Passes:
    """How a judging pass reaches the model.

    Every pass — batch, per finding, final — is the same request with different
    contents, so the plumbing lives here once and the modes are left with what
    they actually decide.
    """

    settings: JudgeSettings
    prompts: Prompts
    diff: DiffBundle
    runner: Runner
    tools: list[dict[str, Any]]
    emit: EventSink

    async def ask(
        self,
        *,
        system: str,
        prompt: str,
        terminal_tool: dict[str, Any],
        metadata: dict[str, Any],
        label: str,
    ) -> AgentOutcome:
        request = AgentRequest(
            system=system,
            prompt=prompt,
            tools=self.tools,
            terminal_tool=terminal_tool,
            model=self.settings.model,
            max_turns=self.settings.max_turns,
            enable_thinking=self.settings.enable_thinking,
            metadata=metadata,
        )
        return await self.runner.run(request, self._progress(label))

    def announce(self, message: str) -> None:
        self.emit(Event("judge_start", message))

    def failed(self, message: str) -> None:
        self.emit(Event("error", message))

    def _progress(self, label: str) -> Callable[[str, str], None]:
        def progress(kind: str, detail: str) -> None:
            self.emit(Event("item_progress", f"{label} {kind}: {detail}", item_id="__judge__"))

        return progress


def judge_for(passes: Passes) -> Judge:
    """The judge the configured mode asks for."""
    if passes.settings.mode == "two_stage":
        return TwoStageJudge(passes)
    return BatchJudge(passes)


@dataclass
class BatchJudge:
    """One pass over the whole list."""

    passes: Passes

    async def rule(self, run: ReviewRun) -> None:
        self.passes.announce(f"The judge is checking {len(run.findings)} findings")

        outcome = await self.passes.ask(
            system=self.passes.prompts.judge_system,
            prompt=self.passes.prompts.build_judge_prompt(run.findings, self.passes.diff),
            terminal_tool=SUBMIT_VERDICTS_TOOL,
            metadata={"stage": "judge"},
            label="judge",
        )
        run.judge_usage = outcome.usage

        if not outcome.ok or outcome.payload is None:
            # The judge failed — show findings as they are instead of losing the run
            run.verdicts = {
                f.id: Verdict(finding_id=f.id, verdict="unreviewed") for f in run.findings
            }
            run.judge_summary = (
                f"The judge failed: {outcome.error}. Findings are shown unfiltered."
            )
            self.passes.failed(run.judge_summary)
            return

        run.judge_summary, verdicts = _verdicts_from_payload(outcome.payload)
        for finding in run.findings:
            verdict = verdicts.get(finding.id)
            if verdict is None:
                verdicts[finding.id] = Verdict(
                    finding_id=finding.id, verdict="unreviewed",
                    reason="the judge returned no verdict",
                )
                continue
            if verdict.severity is not None and verdict.severity != finding.severity:
                finding.severity = Severity(verdict.severity)
        run.verdicts = verdicts


@dataclass
class TwoStageJudge:
    """A pass per finding, then one pass over what survived."""

    passes: Passes

    async def rule(self, run: ReviewRun) -> None:
        run.verdicts = await self._verify_each(run)
        # Only what the author would otherwise see. Findings already rejected stay
        # rejected: re-showing them invites the second pass to reinstate a claim
        # the verification pass spent a whole turn budget killing.
        survivors = run.confirmed()
        prose = await self._rule_on_survivors(run, survivors) if survivors else ""
        run.judge_summary = _summary(run, prose)

    async def _verify_each(self, run: ReviewRun) -> dict[str, Verdict]:
        """One pass per finding, each settling its own claim.

        Severity corrections land on the findings and usage on the run; the
        verdicts come back to the caller.
        """
        self.passes.announce(
            f"Stage 1 of 2: checking {len(run.findings)} findings, one pass each"
        )

        semaphore = asyncio.Semaphore(max(1, self.passes.settings.concurrency))
        verdicts: dict[str, Verdict] = {}
        corrections: dict[str, Severity] = {}

        async def judge_one(finding: Finding) -> Usage:
            others = [f for f in run.findings if f.id != finding.id]
            async with semaphore:
                outcome = await self.passes.ask(
                    system=self.passes.prompts.judge_one_system,
                    prompt=self.passes.prompts.build_judge_one_prompt(
                        finding, others, self.passes.diff
                    ),
                    terminal_tool=SUBMIT_VERDICT_TOOL,
                    metadata={"stage": "judge", "finding_id": finding.id},
                    label=f"judge {finding.id}",
                )

            if not outcome.ok or outcome.payload is None:
                # One pass failing leaves its finding unjudged rather than
                # dropping it — an unreviewed finding still reaches the author.
                verdicts[finding.id] = Verdict(
                    finding_id=finding.id,
                    verdict="unreviewed",
                    reason=f"the judging pass failed: {outcome.error or 'no result'}",
                )
                self.passes.failed(f"Judging {finding.id} failed: {outcome.error}")
                return outcome.usage

            verdict = _verdict_from_payload(outcome.payload, finding.id)
            if verdict is None:
                verdicts[finding.id] = Verdict(
                    finding_id=finding.id,
                    verdict="unreviewed",
                    reason="the judging pass returned a malformed verdict",
                )
                return outcome.usage

            verdicts[finding.id] = verdict
            if verdict.severity is not None and verdict.severity != finding.severity:
                corrections[finding.id] = Severity(verdict.severity)
            return outcome.usage

        usages = await asyncio.gather(*(judge_one(f) for f in run.findings))

        # Applied after the fan-out: severity decides report order, and mutating
        # findings from inside concurrent tasks would make that order depend on
        # which pass happened to finish first.
        for finding in run.findings:
            if finding.id in corrections:
                finding.severity = corrections[finding.id]

        for usage in usages:
            run.judge_usage = run.judge_usage + usage
        return verdicts

    async def _rule_on_survivors(self, run: ReviewRun, survivors: list[Finding]) -> str:
        """The second stage: one pass over the findings that survived verification.

        Applies its verdicts and severities to the run and returns its assessment
        of the merge request, which the caller puts under the tally.
        """
        self.passes.announce(f"Stage 2 of 2: ruling on {len(survivors)} verified findings")

        # What each finding's own pass reported checking. The second stage reads
        # it instead of repeating the search, and can see when a note settles
        # something narrower than the finding it was attached to.
        notes = {
            f.id: run.verdicts[f.id].reason
            for f in survivors
            if f.id in run.verdicts and run.verdicts[f.id].reason
        }

        outcome = await self.passes.ask(
            system=self.passes.prompts.judge_final_system,
            prompt=self.passes.prompts.build_judge_final_prompt(
                survivors, notes, self.passes.diff
            ),
            terminal_tool=SUBMIT_VERDICTS_TOOL,
            metadata={"stage": "judge", "pass": "final"},
            label="judge final",
        )
        run.judge_usage = run.judge_usage + outcome.usage

        if not outcome.ok or outcome.payload is None:
            # Verification already happened, so a failure here costs calibration,
            # not the verdicts. Say which of the two the report is missing.
            self.passes.failed(f"The final judging pass failed: {outcome.error}")
            return (
                f"The pass that rules on the set as a whole failed ({outcome.error}), "
                f"so severities are the ones each finding was given on its own."
            )

        summary, verdicts = _verdicts_from_payload(outcome.payload)
        for finding in survivors:
            final = verdicts.get(finding.id)
            if final is not None:
                _apply_ruling(run, finding, final)
        return summary


def _apply_ruling(run: ReviewRun, finding: Finding, final: Verdict) -> None:
    """What the second stage decided about one finding it was shown."""
    verified = run.verdicts.get(finding.id)
    recalibrated = final.severity is not None and final.severity != finding.severity
    if final.severity is not None:
        finding.severity = Severity(final.severity)

    # The reason has to describe the state the report ends up showing. If this
    # pass changed nothing, the one that actually checked the code said it
    # better — keep it. If it moved the verdict or the severity, the earlier text
    # now narrates a decision that was overruled, and showing "lowered to minor"
    # next to a Nit badge is the kind of self-contradiction a judging layer
    # exists to prevent.
    unchanged = (
        verified is not None
        and not recalibrated
        and final.verdict == verified.verdict
        and bool(verified.reason)
    )
    reason = verified.reason if unchanged and verified is not None else final.reason

    run.verdicts[finding.id] = Verdict(
        finding_id=finding.id,
        verdict=final.verdict,
        severity=finding.severity,
        reason=reason,
    )


def _verdict_from_payload(payload: dict[str, Any], finding_id: str) -> Verdict | None:
    """One verdict, from a pass that was told about exactly one finding. The id is
    ours, not the model's: echoing it back would only be a chance to get it wrong."""
    try:
        return Verdict.model_validate({**payload, "finding_id": finding_id})
    except Exception:  # noqa: BLE001 — a malformed verdict must not sink the finding
        return None


def _verdicts_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Verdict]]:
    summary = str(payload.get("summary", "")).strip()
    verdicts: dict[str, Verdict] = {}
    for raw in payload.get("verdicts") or []:
        if not isinstance(raw, dict):
            continue
        try:
            verdict = Verdict.model_validate(raw)
        except Exception:  # noqa: BLE001
            continue
        verdicts[verdict.finding_id] = verdict
    return summary, verdicts


def _summary(run: ReviewRun, prose: str) -> str:
    """Counted after both stages, so the numbers match the report rather than the
    intermediate state — which stage rejected a finding is in its own verdict."""
    head = (
        f"Each of the {len(run.findings)} findings was checked by its own pass, then "
        f"the survivors were ruled on as a set: {_tally(run)}."
    )
    return f"{head}\n\n{prose}" if prose else head


def _tally(run: ReviewRun) -> str:
    """How the verdicts came out, as prose. Empty buckets are left out rather
    than printed as zero."""
    counts = Counter(v.verdict for v in run.verdicts.values())
    labels: list[tuple[Any, str]] = [
        ("confirmed", "confirmed"),
        ("nitpick", "downgraded to nitpicks"),
        ("false_positive", "rejected as false positives"),
        ("duplicate", "rejected as duplicates"),
        ("unreviewed", "left unjudged because the pass failed"),
    ]
    return ", ".join(f"{counts[key]} {label}" for key, label in labels if counts[key])
