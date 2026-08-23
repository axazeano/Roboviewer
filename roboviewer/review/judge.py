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
pass reaches the model, which is `JudgeContext`, and nothing else. Adding a
third way means adding a class, not editing the one that picks.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import ModelConfig
from ..models import Finding, ReviewRun, Severity, Usage, Verdict
from ..observer import SILENT, RunObserver
from ..provider import AgentOutcome, AgentRequest, Runner
from ..repo import ChangeSet
from .prompts import Prompts
from .prompts.tool_schemas import SUBMIT_VERDICT_TOOL, SUBMIT_VERDICTS_TOOL
from .prompts.turns import TURN_NOTES
from .submissions import verdict_from_payload, verdicts_from_payload


class Judge(Protocol):
    """What a run needs from its judging stage.

    `rule` fills in `run.verdicts`, `run.judge_summary` and any corrected
    severity. It answers in the run rather than in a return value because a
    verdict is only meaningful next to the finding it belongs to.
    """

    async def rule(self, run: ReviewRun) -> None: ...


@dataclass(frozen=True)
class JudgeSettings:
    """What judging takes out of the config.

    Named apart so a judging pass can be built and tested without a whole
    `Config`. `model` is a whole `ModelConfig` rather than a handful of copied
    fields, which is what lets the judge differ from the reviewers in anything
    at all — down to temperature — without another field appearing here.
    """

    mode: str
    model: ModelConfig
    concurrency: int


@dataclass
class JudgeContext:
    """Everything a judging pass needs to reach the model, and the one way to.

    Every pass — batch, per finding, final — is the same request with different
    contents, so the plumbing lives here once and the modes are left with what
    they actually decide.
    """

    settings: JudgeSettings
    prompts: Prompts
    changes: ChangeSet
    runner: Runner
    tools: list[dict[str, Any]]
    # Judging passes are agents like any other and report like any other; what
    # tells them apart to an observer is that they say which they are.
    observer: RunObserver = SILENT

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
            settings=self.settings.model,
            notes=TURN_NOTES,
            metadata=metadata,
            observer=self.observer.agent("judge", label, str(metadata.get("finding_id", ""))),
        )
        return await self.runner.run(request)

    def announce(self, message: str) -> None:
        self.observer.judging(message)

    def failed(self, message: str) -> None:
        self.observer.failed(message)


def judge_for(context: JudgeContext) -> Judge:
    """The judge the configured mode asks for."""
    if context.settings.mode == "two_stage":
        return TwoStageJudge(context)
    return BatchJudge(context)


@dataclass
class BatchJudge:
    """One pass over the whole list."""

    context: JudgeContext

    async def rule(self, run: ReviewRun) -> None:
        self.context.announce(f"The judge is checking {len(run.findings)} findings")

        outcome = await self.context.ask(
            system=self.context.prompts.judge_system,
            prompt=self.context.prompts.build_judge_prompt(run.findings, self.context.changes),
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
            self.context.failed(run.judge_summary)
            return

        run.judge_summary, verdicts = verdicts_from_payload(outcome.payload)
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

    context: JudgeContext

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
        self.context.announce(
            f"Stage 1 of 2: checking {len(run.findings)} findings, one pass each"
        )

        semaphore = asyncio.Semaphore(max(1, self.context.settings.concurrency))
        verdicts: dict[str, Verdict] = {}
        corrections: dict[str, Severity] = {}

        async def judge_one(finding: Finding) -> Usage:
            others = [f for f in run.findings if f.id != finding.id]
            async with semaphore:
                outcome = await self.context.ask(
                    system=self.context.prompts.judge_one_system,
                    prompt=self.context.prompts.build_judge_one_prompt(
                        finding, others, self.context.changes
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
                self.context.failed(f"Judging {finding.id} failed: {outcome.error}")
                return outcome.usage

            verdict = verdict_from_payload(outcome.payload, finding.id)
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
        self.context.announce(f"Stage 2 of 2: ruling on {len(survivors)} verified findings")

        # What each finding's own pass reported checking. The second stage reads
        # it instead of repeating the search, and can see when a note settles
        # something narrower than the finding it was attached to.
        notes = {
            f.id: run.verdicts[f.id].reason
            for f in survivors
            if f.id in run.verdicts and run.verdicts[f.id].reason
        }

        outcome = await self.context.ask(
            system=self.context.prompts.judge_final_system,
            prompt=self.context.prompts.build_judge_final_prompt(
                survivors, notes, self.context.changes
            ),
            terminal_tool=SUBMIT_VERDICTS_TOOL,
            metadata={"stage": "judge", "pass": "final"},
            label="judge final",
        )
        run.judge_usage = run.judge_usage + outcome.usage

        if not outcome.ok or outcome.payload is None:
            # Verification already happened, so a failure here costs calibration,
            # not the verdicts. Say which of the two the report is missing.
            self.context.failed(f"The final judging pass failed: {outcome.error}")
            return (
                f"The pass that rules on the set as a whole failed ({outcome.error}), "
                f"so severities are the ones each finding was given on its own."
            )

        summary, verdicts = verdicts_from_payload(outcome.payload)
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
