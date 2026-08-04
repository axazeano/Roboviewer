"""Orchestration: fan out over checklist items → merge → judge → report.

What each stage does is elsewhere — the agents in `runners`, the verdicts in
`judge`. This is the order they run in and what is carried between them.
"""

from __future__ import annotations

import asyncio
import difflib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .checklist import ChecklistItem
from .config import Config
from .events import Event, EventSink, noop
from .gitdiff import DiffBundle, in_scope
from .judge import JudgeSettings, Passes, judge_for
from .models import SEVERITY_ORDER, Finding, ItemResult, ReviewRun, Verdict
from .prompts import Prompts
from .runners import AgentRequest, Runner
from .tools import SUBMIT_FINDINGS_TOOL, tool_schemas

# --------------------------------------------------------------------------- merge


def merge_findings(results: list[ItemResult], min_confidence: float = 0.0) -> list[Finding]:
    """Collapses duplicates: different checklist items often flag the same line.

    Grouping is coarse (file plus a 5-line window); inside a group titles and
    rationales are compared. The threshold is deliberately conservative — better
    to show two similar findings than to lose a real one.
    """
    merged: list[Finding] = []
    for group in _by_place(_reported(results, min_confidence)).values():
        merged.extend(_collapse(group))

    merged.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0))
    for index, finding in enumerate(merged, start=1):
        finding.id = f"F{index:03d}"
    return merged


def _reported(results: list[ItemResult], min_confidence: float) -> list[Finding]:
    """Everything worth carrying forward, each finding knowing who found it."""
    flat: list[Finding] = []
    for result in results:
        for finding in result.findings:
            if finding.confidence < min_confidence:
                continue
            finding.sources = finding.sources or [result.item_id]
            flat.append(finding)
    return flat


def _by_place(findings: list[Finding]) -> dict[tuple[str, int], list[Finding]]:
    """Grouped by file and a 5-line window.

    Coarse on purpose: comparing every finding against every other one is
    quadratic, and two remarks about unrelated lines of the same file are not
    duplicates however alike they read.
    """
    buckets: dict[tuple[str, int], list[Finding]] = {}
    for finding in findings:
        buckets.setdefault(finding.dedupe_bucket(), []).append(finding)
    return buckets


def _collapse(group: list[Finding]) -> list[Finding]:
    """One place, several wordings: keep one of each problem, the strongest."""
    kept: list[Finding] = []
    for finding in group:
        twin = _twin_of(finding, kept)
        if twin is None:
            kept.append(finding)
        elif _stronger(finding, twin):
            finding.sources = _sources(twin, finding)
            kept[kept.index(twin)] = finding
        else:
            twin.sources = _sources(twin, finding)
    return kept


def _twin_of(finding: Finding, kept: list[Finding]) -> Finding | None:
    """The same problem already kept, in other words.

    Either the titles read alike or the rationales do — the rationale is the
    longer text, so it takes a higher bar to count.
    """
    return next(
        (
            k
            for k in kept
            if _similar(k.title, finding.title) > 0.7
            or _similar(k.rationale[:240], finding.rationale[:240]) > 0.8
        ),
        None,
    )


def _stronger(finding: Finding, twin: Finding) -> bool:
    """Heavier severity wins; at equal severity, the more confident wording."""
    return (SEVERITY_ORDER[finding.severity], -finding.confidence) < (
        SEVERITY_ORDER[twin.severity],
        -twin.confidence,
    )


def _sources(*findings: Finding) -> list[str]:
    """Every item that reported it, so the report can say who agreed."""
    return sorted({item for finding in findings for item in finding.sources})


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


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
        self._emit = on_event or noop
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
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
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
            run.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
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
            run.verdicts = {
                f.id: Verdict(finding_id=f.id, verdict="unreviewed") for f in run.findings
            }

        run.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._emit(Event("run_done", "Review finished", data={"run": run}))
        return run

    def _split_by_scope(self, findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
        """In-scope first, then the rest — ids are reassigned so the report
        numbers what it shows. Judging runs on the first list only: verifying a
        remark about code the MR never touched costs a pass to earn a line the
        author has no reason to act on in this review."""
        margin = self._cfg.run.scope_margin
        keep: list[Finding] = []
        drop: list[Finding] = []
        for finding in findings:
            reachable = in_scope(self._diff.changes, finding.file, finding.line, margin)
            (keep if reachable else drop).append(finding)

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
            self._emit(
                Event("item_progress", f"{kind}: {detail}", item_id=item.id, data={"kind": kind})
            )

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
        passes = Passes(
            settings=JudgeSettings(
                mode=self._cfg.run.judge_mode,
                model=self._cfg.provider.resolve_judge_model(),
                max_turns=self._cfg.run.resolve_judge_max_turns(),
                enable_thinking=self._cfg.provider.resolve_judge_enable_thinking(),
                concurrency=self._cfg.run.concurrency,
            ),
            prompts=self._prompts,
            diff=self._diff,
            runner=self._runner,
            tools=self._tools,
            emit=self._emit,
        )
        await judge_for(passes).rule(run)

        # Report order, applied after judging rather than inside it: the judge may
        # have moved a finding up or down the scale, and which order the report
        # wants is not something a judging mode should have an opinion about.
        run.findings.sort(
            key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0)
        )
        confirmed = len(run.confirmed())
        self._emit(
            Event(
                "judge_done",
                f"Confirmed {confirmed} of {len(run.findings)}",
                data={"confirmed": confirmed, "total": len(run.findings)},
            )
        )


def output_dir_for(cfg: Config, root: Path, run_id: str) -> Path:
    base = Path(cfg.run.output_dir).expanduser()
    if not base.is_absolute():
        return root / base / run_id
    # A shared external report directory may serve several repositories, so nest by
    # repo name: otherwise runs would interleave and the latest symlinks would fight
    return base / root.name / run_id
