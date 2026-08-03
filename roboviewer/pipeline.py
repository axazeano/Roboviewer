"""Orchestration: fan out over checklist items → merge → judge → report."""

from __future__ import annotations

import asyncio
import difflib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from .checklist import ChecklistItem
from .config import Config
from .gitdiff import DiffBundle
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
from .tools import SUBMIT_FINDINGS_TOOL, SUBMIT_VERDICTS_TOOL, tool_schemas

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

        if run.findings and self._cfg.run.enable_judge:
            await self._run_judge(run)
        else:
            run.verdicts = {f.id: Verdict(finding_id=f.id, verdict="unreviewed") for f in run.findings}

        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._emit(Event("run_done", "Review finished", data={"run": run}))
        return run

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
        result.status = "ok"
        return result

    async def _run_judge(self, run: ReviewRun) -> None:
        self._emit(Event("judge_start", f"The judge is checking {len(run.findings)} findings"))

        request = AgentRequest(
            system=self._prompts.judge_system,
            prompt=self._prompts.build_judge_prompt(run.findings, self._diff),
            tools=self._tools,
            terminal_tool=SUBMIT_VERDICTS_TOOL,
            model=self._cfg.provider.resolve_judge_model(),
            max_turns=self._cfg.run.max_turns,
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
        run.findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0))

        confirmed = len(run.confirmed())
        self._emit(
            Event(
                "judge_done",
                f"Confirmed {confirmed} of {len(run.findings)}",
                data={"confirmed": confirmed, "total": len(run.findings)},
            )
        )


def total_usage(run: ReviewRun) -> Usage:
    return run.total_usage


def output_dir_for(cfg: Config, root: Path, run_id: str) -> Path:
    base = Path(cfg.run.output_dir).expanduser()
    if not base.is_absolute():
        return root / base / run_id
    # A shared external report directory may serve several repositories, so nest by
    # repo name: otherwise runs would interleave and the latest symlinks would fight
    return base / root.name / run_id
