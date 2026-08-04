"""Orchestration: fan out over checklist items → merge → judge → report."""

from __future__ import annotations

import asyncio
import difflib
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from .checklist import ChecklistItem
from .config import Config
from .gitdiff import DiffBundle, in_scope
from .models import (
    SEVERITY_ORDER,
    Finding,
    ItemResult,
    ReviewRun,
    Severity,
    Usage,
    Verdict,
)
from .prompts import Prompts
from .runners import AgentRequest, Runner
from .tools import (
    SUBMIT_FINDINGS_TOOL,
    SUBMIT_VERDICT_TOOL,
    SUBMIT_VERDICTS_TOOL,
    tool_schemas,
)

EventKind = Literal[
    "run_start", "item_start", "item_progress", "item_done",
    "merge_done", "judge_start", "judge_done", "run_done", "error",
]


@dataclass
class Event:
    kind: EventKind
    message: str = ""
    item_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


def _noop(_: Event) -> None:
    return None


# --------------------------------------------------------------------------- merge


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def merge_findings(results: list[ItemResult], min_confidence: float = 0.0) -> list[Finding]:
    """Collapses duplicates: different checklist items often flag the same line.

    Grouping is coarse (file plus a 5-line window); inside a group titles and
    rationales are compared. The threshold is deliberately conservative — better
    to show two similar findings than to lose a real one.
    """
    flat: list[Finding] = []
    for result in results:
        for finding in result.findings:
            if finding.confidence < min_confidence:
                continue
            finding.sources = finding.sources or [result.item_id]
            flat.append(finding)

    buckets: dict[tuple[str, int], list[Finding]] = {}
    for finding in flat:
        buckets.setdefault(finding.dedupe_bucket(), []).append(finding)

    merged: list[Finding] = []
    for group in buckets.values():
        kept: list[Finding] = []
        for finding in group:
            twin = next(
                (
                    k
                    for k in kept
                    if _similar(k.title, finding.title) > 0.7
                    or _similar(k.rationale[:240], finding.rationale[:240]) > 0.8
                ),
                None,
            )
            if twin is None:
                kept.append(finding)
                continue
            # Keep the heavier, more confident wording
            if (SEVERITY_ORDER[finding.severity], -finding.confidence) < (
                SEVERITY_ORDER[twin.severity],
                -twin.confidence,
            ):
                finding.sources = sorted(set(twin.sources) | set(finding.sources))
                kept[kept.index(twin)] = finding
            else:
                twin.sources = sorted(set(twin.sources) | set(finding.sources))
        merged.extend(kept)

    merged.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0))
    for index, finding in enumerate(merged, start=1):
        finding.id = f"F{index:03d}"
    return merged


# --------------------------------------------------------------------------- parsing


def _findings_from_payload(payload: dict[str, Any], item_id: str) -> tuple[str, list[Finding]]:
    summary = str(payload.get("summary", "")).strip()
    raw_items = payload.get("findings") or []
    findings: list[Finding] = []
    if not isinstance(raw_items, list):
        return summary, findings
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            finding = Finding.model_validate({**raw, "sources": [item_id]})
        except Exception:  # noqa: BLE001 — one malformed entry must not sink the whole item
            continue
        findings.append(finding)
    return summary, findings


def _verdict_from_payload(payload: dict[str, Any], finding_id: str) -> Verdict | None:
    """One verdict, from a per-finding judge. The id is ours, not the model's:
    the pass was told about exactly one finding, so echoing an id back would only
    be an opportunity to get it wrong."""
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


# --------------------------------------------------------------------------- pipeline


class ReviewPipeline:
    def __init__(
        self,
        config: Config,
        diff: DiffBundle,
        items: list[ChecklistItem],
        runner: Runner,
        on_event: EventSink | None = None,
        prompts: Prompts | None = None,
    ) -> None:
        self._cfg = config
        self._diff = diff
        self._items = items
        self._runner = runner
        self._emit = on_event or _noop
        self._prompts = prompts or Prompts.load()
        self._tools = tool_schemas(diff.base_sha)

    async def execute(self) -> ReviewRun:
        run = ReviewRun(
            run_id=datetime.now().strftime("%Y%m%d-%H%M%S"),
            repo_root=str(self._diff.root),
            branch=self._diff.branch,
            target=self._diff.target,
            base_sha=self._diff.base_sha,
            head_sha=self._diff.head,
            model=self._cfg.provider.model,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            files=self._diff.files,
            items=[ItemResult(item_id=i.id, item_title=i.title) for i in self._items],
        )

        self._emit(
            Event(
                "run_start",
                f"{self._diff.branch} → {self._diff.target}: {len(self._diff.files)} files, "
                f"{len(self._items)} checklist items",
                data={"files": len(self._diff.files), "items": len(self._items)},
            )
        )

        if not self._diff.files:
            run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._emit(Event("run_done", "No changes relative to the target branch"))
            return run

        semaphore = asyncio.Semaphore(max(1, self._cfg.run.concurrency))
        slot_of = {item.id: index for index, item in enumerate(self._items)}

        async def worker(item: ChecklistItem) -> None:
            async with semaphore:
                result = await self._run_item(item)
                run.items[slot_of[item.id]] = result
                self._emit(
                    Event(
                        "item_done",
                        f"{item.title}: {len(result.findings)} findings ({result.status})",
                        item_id=item.id,
                        data={"result": result},
                    )
                )

        await asyncio.gather(*(worker(item) for item in self._items))

        run.findings = merge_findings(run.items, self._cfg.run.min_confidence)
        self._emit(
            Event(
                "merge_done",
                f"After merge and deduplication: {len(run.findings)} findings",
                data={"count": len(run.findings)},
            )
        )

        if self._cfg.run.enforce_scope:
            run.findings, run.out_of_scope = self._split_by_scope(run.findings)
            if run.out_of_scope:
                self._emit(
                    Event(
                        "merge_done",
                        f"{len(run.out_of_scope)} pointed outside the changed lines "
                        f"and are listed separately",
                        data={"count": len(run.out_of_scope)},
                    )
                )

        if run.findings and self._cfg.run.enable_judge:
            await self._run_judge(run)
        else:
            run.verdicts = {f.id: Verdict(finding_id=f.id, verdict="unreviewed") for f in run.findings}

        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._emit(Event("run_done", "Review finished", data={"run": run}))
        return run

    def _split_by_scope(self, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        """In-scope first, then the rest — ids are reassigned so the report
        numbers what it shows. Judging runs on the first list only: verifying a
        remark about code the MR never touched costs a pass to earn a line the
        author has no reason to act on in this review."""
        margin = self._cfg.run.scope_margin
        keep, drop = [], []
        for finding in findings:
            target = keep if in_scope(self._diff.changes, finding.file, finding.line, margin) else drop
            target.append(finding)

        for index, finding in enumerate(keep, start=1):
            finding.id = f"F{index:03d}"
        for index, finding in enumerate(drop, start=1):
            finding.id = f"X{index:03d}"
        return keep, drop

    # ------------------------------------------------------------------ stages

    async def _run_item(self, item: ChecklistItem) -> ItemResult:
        self._emit(Event("item_start", f"Started: {item.title}", item_id=item.id))
        started = time.monotonic()
        result = ItemResult(item_id=item.id, item_title=item.title, status="running")

        request = AgentRequest(
            system=self._prompts.system_for(item),
            prompt=self._prompts.build_item_prompt(item, self._diff),
            tools=self._tools,
            terminal_tool=SUBMIT_FINDINGS_TOOL,
            model=self._cfg.provider.model,
            max_turns=self._cfg.run.max_turns,
            enable_thinking=self._cfg.provider.enable_thinking,
            metadata={"item_id": item.id},
        )

        def progress(kind: str, detail: str) -> None:
            self._emit(Event("item_progress", f"{kind}: {detail}", item_id=item.id, data={"kind": kind}))

        outcome = await self._runner.run(request, progress)
        result.usage = outcome.usage
        result.turns = outcome.turns
        result.duration_s = time.monotonic() - started

        if not outcome.ok or outcome.payload is None:
            result.status = "failed"
            result.error = outcome.error or "the agent returned no result"
            self._emit(Event("error", f"{item.title}: {result.error}", item_id=item.id))
            return result

        result.summary, result.findings = _findings_from_payload(outcome.payload, item.id)
        result.status = "truncated" if outcome.truncated else "ok"
        return result

    async def _run_judge(self, run: ReviewRun) -> None:
        mode = self._cfg.run.judge_mode
        if mode == "per_finding":
            await self._run_judge_per_finding(run)
        elif mode == "two_stage":
            await self._run_judge_two_stage(run)
        else:
            await self._run_judge_batch(run)

        confirmed = len(run.confirmed())
        self._emit(
            Event(
                "judge_done",
                f"Confirmed {confirmed} of {len(run.findings)}",
                data={"confirmed": confirmed, "total": len(run.findings)},
            )
        )

    async def _run_judge_per_finding(self, run: ReviewRun) -> None:
        """One agent per finding. Each gets the full turn budget for a single
        claim, and a failure costs one verdict instead of all of them."""
        run.verdicts = await self._verify_each(
            run, f"Checking {len(run.findings)} findings, one pass each"
        )
        run.judge_summary = _per_finding_summary(run)
        _sort_by_severity(run.findings)

    async def _run_judge_two_stage(self, run: ReviewRun) -> None:
        """Verify each claim on its own, then let one judge rule on what survived.

        A pass holding a single finding settles facts but has no scale: it cannot
        tell a blocker from a nit with nothing to compare against, and five
        copies of the same complaint each read as reasonable in isolation. The
        second pass sees the survivors together — severity relative to each
        other, repetition, and an assessment of the MR as a whole, which no
        per-finding pass is in a position to write.
        """
        run.verdicts = await self._verify_each(
            run, f"Stage 1 of 2: checking {len(run.findings)} findings, one pass each"
        )
        # Only what the author would otherwise see. Findings already rejected stay
        # rejected: re-showing them invites the second pass to reinstate a claim
        # the verification pass spent a whole turn budget killing.
        survivors = run.confirmed()
        prose = await self._rule_on_survivors(run, survivors) if survivors else ""
        run.judge_summary = _two_stage_summary(run, prose)
        _sort_by_severity(run.findings)

    async def _verify_each(self, run: ReviewRun, message: str) -> dict[str, Verdict]:
        """The fan-out both per-finding modes share: one pass per finding, each
        settling its own claim. Severity corrections land on the findings and
        usage on the run; the verdicts come back to the caller."""
        self._emit(Event("judge_start", message))

        semaphore = asyncio.Semaphore(max(1, self._cfg.run.concurrency))
        verdicts: dict[str, Verdict] = {}
        corrections: dict[str, Severity] = {}

        async def judge_one(finding: Finding) -> Usage:
            others = [f for f in run.findings if f.id != finding.id]
            request = AgentRequest(
                system=self._prompts.judge_one_system,
                prompt=self._prompts.build_judge_one_prompt(finding, others, self._diff),
                tools=self._tools,
                terminal_tool=SUBMIT_VERDICT_TOOL,
                model=self._cfg.provider.resolve_judge_model(),
                max_turns=self._cfg.run.resolve_judge_max_turns(),
                enable_thinking=self._cfg.provider.resolve_judge_enable_thinking(),
                metadata={"stage": "judge", "finding_id": finding.id},
            )

            def progress(kind: str, detail: str) -> None:
                self._emit(
                    Event("item_progress", f"judge {finding.id} {kind}: {detail}",
                          item_id="__judge__")
                )

            async with semaphore:
                outcome = await self._runner.run(request, progress)

            if not outcome.ok or outcome.payload is None:
                # One pass failing leaves its finding unjudged rather than
                # dropping it — an unreviewed finding still reaches the author.
                verdicts[finding.id] = Verdict(
                    finding_id=finding.id,
                    verdict="unreviewed",
                    reason=f"the judging pass failed: {outcome.error or 'no result'}",
                )
                self._emit(Event("error", f"Judging {finding.id} failed: {outcome.error}"))
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
        self._emit(Event("judge_start", f"Stage 2 of 2: ruling on {len(survivors)} verified findings"))

        # What each finding's own pass reported checking. The second stage reads
        # it instead of repeating the search, and can see when a note settles
        # something narrower than the finding it was attached to.
        notes = {
            f.id: run.verdicts[f.id].reason
            for f in survivors
            if f.id in run.verdicts and run.verdicts[f.id].reason
        }

        request = AgentRequest(
            system=self._prompts.judge_final_system,
            prompt=self._prompts.build_judge_final_prompt(survivors, notes, self._diff),
            tools=self._tools,
            terminal_tool=SUBMIT_VERDICTS_TOOL,
            model=self._cfg.provider.resolve_judge_model(),
            max_turns=self._cfg.run.resolve_judge_max_turns(),
            enable_thinking=self._cfg.provider.resolve_judge_enable_thinking(),
            metadata={"stage": "judge", "pass": "final"},
        )

        def progress(kind: str, detail: str) -> None:
            self._emit(Event("item_progress", f"judge final {kind}: {detail}", item_id="__judge__"))

        outcome = await self._runner.run(request, progress)
        run.judge_usage = run.judge_usage + outcome.usage

        if not outcome.ok or outcome.payload is None:
            # Verification already happened, so a failure here costs calibration,
            # not the verdicts. Say which of the two the report is missing.
            self._emit(Event("error", f"The final judging pass failed: {outcome.error}"))
            return (
                f"The pass that rules on the set as a whole failed ({outcome.error}), "
                f"so severities are the ones each finding was given on its own."
            )

        summary, verdicts = _verdicts_from_payload(outcome.payload)
        for finding in survivors:
            final = verdicts.get(finding.id)
            if final is None:
                continue  # no ruling on this one — its own verdict stands

            verified = run.verdicts.get(finding.id)
            recalibrated = final.severity is not None and final.severity != finding.severity
            if final.severity is not None:
                finding.severity = Severity(final.severity)

            # The reason has to describe the state the report ends up showing. If
            # this pass changed nothing, the one that actually checked the code
            # said it better — keep it. If it moved the verdict or the severity,
            # the earlier text now narrates a decision that was overruled, and
            # showing "lowered to minor" next to a Nit badge is the kind of
            # self-contradiction a judging layer exists to prevent.
            unchanged = (
                verified is not None
                and not recalibrated
                and final.verdict == verified.verdict
                and bool(verified.reason)
            )
            reason = verified.reason if unchanged else final.reason

            run.verdicts[finding.id] = Verdict(
                finding_id=finding.id,
                verdict=final.verdict,
                severity=finding.severity,
                reason=reason,
            )

        return summary

    async def _run_judge_batch(self, run: ReviewRun) -> None:
        self._emit(Event("judge_start", f"The judge is checking {len(run.findings)} findings"))

        request = AgentRequest(
            system=self._prompts.judge_system,
            prompt=self._prompts.build_judge_prompt(run.findings, self._diff),
            tools=self._tools,
            terminal_tool=SUBMIT_VERDICTS_TOOL,
            model=self._cfg.provider.resolve_judge_model(),
            max_turns=self._cfg.run.resolve_judge_max_turns(),
            enable_thinking=self._cfg.provider.resolve_judge_enable_thinking(),
            metadata={"stage": "judge"},
        )

        def progress(kind: str, detail: str) -> None:
            self._emit(Event("item_progress", f"judge {kind}: {detail}", item_id="__judge__"))

        outcome = await self._runner.run(request, progress)
        run.judge_usage = outcome.usage

        if not outcome.ok or outcome.payload is None:
            # The judge failed — show findings as they are instead of losing the run
            run.verdicts = {f.id: Verdict(finding_id=f.id, verdict="unreviewed") for f in run.findings}
            run.judge_summary = (
                f"The judge failed: {outcome.error}. Findings are shown unfiltered."
            )
            self._emit(Event("error", run.judge_summary))
            return

        run.judge_summary, verdicts = _verdicts_from_payload(outcome.payload)
        for finding in run.findings:
            verdict = verdicts.get(finding.id)
            if verdict is None:
                verdicts[finding.id] = Verdict(finding_id=finding.id, verdict="unreviewed",
                                               reason="the judge returned no verdict")
                continue
            if verdict.severity is not None and verdict.severity != finding.severity:
                finding.severity = Severity(verdict.severity)
        run.verdicts = verdicts
        _sort_by_severity(run.findings)


def _sort_by_severity(findings: list[Finding]) -> None:
    """Report order, applied after every judging mode: the judge may have moved a
    finding up or down the scale, and the order has to follow the corrected value."""
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0))


def _verdict_tally(run: ReviewRun) -> str:
    """How the verdicts came out, as prose. Empty buckets are left out rather
    than printed as zero."""
    counts = Counter(v.verdict for v in run.verdicts.values())
    labels = [
        ("confirmed", "confirmed"),
        ("nitpick", "downgraded to nitpicks"),
        ("false_positive", "rejected as false positives"),
        ("duplicate", "rejected as duplicates"),
        ("unreviewed", "left unjudged because the pass failed"),
    ]
    return ", ".join(f"{counts[key]} {label}" for key, label in labels if counts[key])


def _per_finding_summary(run: ReviewRun) -> str:
    """The batch judge writes an overall assessment; a per-finding judge never
    sees the whole picture, so the report gets the tally instead of prose."""
    return (
        f"Each of the {len(run.findings)} findings was checked by its own pass: "
        f"{_verdict_tally(run)}."
    )


def _two_stage_summary(run: ReviewRun, prose: str) -> str:
    """Counted after the second stage, so the numbers match the report rather
    than the intermediate state — which of the two stages rejected a finding is
    in that finding's own verdict."""
    head = (
        f"Each of the {len(run.findings)} findings was checked by its own pass, then "
        f"the survivors were ruled on as a set: {_verdict_tally(run)}."
    )
    return f"{head}\n\n{prose}" if prose else head


def total_usage(run: ReviewRun) -> Usage:
    return run.total_usage


def output_dir_for(cfg: Config, root: Path, run_id: str) -> Path:
    base = Path(cfg.run.output_dir).expanduser()
    if not base.is_absolute():
        return root / base / run_id
    # A shared external report directory may serve several repositories, so nest by
    # repo name: otherwise runs would interleave and the latest symlinks would fight
    return base / root.name / run_id
