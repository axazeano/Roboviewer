"""Orchestration: fan out over checklist items → merge → scope → judge.

What each stage does is elsewhere — the agents in `provider`, the verdicts in
`judge`. This is the order they run in and what is carried between them.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .checklist import ChecklistItem
from .config import Config
from .judge import JudgeContext, JudgeSettings, judge_for
from .models import SEVERITY_ORDER, Finding, ItemResult, ReviewRun, Verdict
from .observer import SILENT, RunObserver
from .prompts import Prompts
from .prompts.tool_schemas import SUBMIT_FINDINGS_TOOL, tool_schemas
from .provider import AgentRequest, Runner
from .repo import ChangeSet
from .review.scope import in_scope

# --------------------------------------------------------------------------- merge

# Lines this far apart can still be one defect written up twice.
MERGE_WINDOW = 5
# How much of the smaller set of identifiers two findings must share to be
# talking about the same thing.
SUBJECT_OVERLAP = 0.5

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[a-z][A-Za-z0-9]*[A-Z]")


class ReviewPipeline:
    def __init__(
        self,
        config: Config,
        changes: ChangeSet,
        items: list[ChecklistItem],
        runner: Runner,
        prompts: Prompts | None = None,
        observer: RunObserver = SILENT,
    ) -> None:
        """`observer` is how something outside the pipeline hears what it does —
        the console, a recorder, both at once; see `observer`. Silence by
        default, and the run keeps no account of itself."""
        self._cfg = config
        self._changes = changes
        self._items = items
        self._runner = runner
        self._prompts = prompts or Prompts.load()
        self._observer = observer
        self._tools = tool_schemas(changes.comparison.base_sha)

    async def execute(self) -> ReviewRun:
        """The steps, in the order they run and with what each hands on."""
        run = self._start_run()
        if not self._changes.files:
            return self._finish_run(run, "No changes relative to the target branch")

        run.items = await self._review_every_item()
        run.findings = merge_findings(run.items)
        self._observer.merged(len(run.findings))
        run.findings, run.out_of_scope = self._split_off_out_of_scope(run.findings)
        await self._judge_findings(run)
        return self._finish_run(run)

    # ------------------------------------------------------------------ steps

    def _start_run(self) -> ReviewRun:
        """The record every step writes into, with a pending result per item."""
        compared = self._changes.comparison
        run = ReviewRun(
            run_id=datetime.now().strftime("%Y%m%d-%H%M%S"),
            repo_root=str(compared.root),
            branch=compared.source,
            target=compared.target,
            base_sha=compared.base_sha,
            head_sha=compared.head_sha,
            model=self._cfg.reviewer.model,
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            files=self._changes.files,
            items=[ItemResult(item_id=i.id, item_title=i.title) for i in self._items],
        )
        # The directory as well as the run: an observer that keeps something has
        # to know where the run's artifacts go without reading the config.
        self._observer.run_started(run, output_dir_for(self._cfg, compared.root, run.run_id))
        return run

    async def _review_every_item(self) -> list[ItemResult]:
        """One agent per checklist item, `concurrency` of them at a time.

        Comes back in checklist order rather than in the order the agents
        finished — the report lists aspects the way the checklist does.
        """
        semaphore = asyncio.Semaphore(max(1, self._cfg.run.concurrency))

        async def review(item: ChecklistItem) -> ItemResult:
            # Reported done before the slot is released, so an item is reported
            # done before the next one is reported started.
            async with semaphore:
                result = await self._review_one_item(item)
                self._observer.item_finished(item.id, item.title, result)
            return result

        return list(await asyncio.gather(*(review(item) for item in self._items)))

    def _split_off_out_of_scope(
        self, findings: list[Finding]
    ) -> tuple[list[Finding], list[Finding]]:
        """(what this MR changed, what it did not) — and nothing split off when
        the gate is switched off.

        Ids are reassigned so the report numbers what it shows. Judging runs on
        the first list only: verifying a remark about code the MR never touched
        costs a pass to earn a line the author has no reason to act on here.
        """
        if not self._cfg.run.enforce_scope:
            return findings, []

        margin = self._cfg.run.scope_margin
        keep: list[Finding] = []
        drop: list[Finding] = []
        for finding in findings:
            reachable = in_scope(self._changes.lines, finding.file, finding.line, margin)
            (keep if reachable else drop).append(finding)

        for index, finding in enumerate(keep, start=1):
            finding.id = f"F{index:03d}"
        for index, finding in enumerate(drop, start=1):
            finding.id = f"X{index:03d}"

        if drop:
            self._observer.out_of_scope(len(drop))
        return keep, drop

    async def _judge_findings(self, run: ReviewRun) -> None:
        """Fills in `run.verdicts`, and with a judge on, `run.judge_summary` and
        any corrected severity. Nothing to judge and nobody to judge it both end
        as `unreviewed`, which is what the report shows."""
        if not (run.findings and self._cfg.run.enable_judge):
            run.verdicts = {
                f.id: Verdict(finding_id=f.id, verdict="unreviewed") for f in run.findings
            }
            return

        context = JudgeContext(
            settings=JudgeSettings(
                mode=self._cfg.run.judge_mode,
                model=self._cfg.for_judge(),
                concurrency=self._cfg.run.concurrency,
            ),
            prompts=self._prompts,
            changes=self._changes,
            runner=self._runner,
            tools=self._tools,
            observer=self._observer,
        )
        await judge_for(context).rule(run)

        # Report order, applied after judging rather than inside it: the judge may
        # have moved a finding up or down the scale, and which order the report
        # wants is not something a judging mode should have an opinion about.
        run.findings.sort(
            key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0)
        )
        self._observer.judged(len(run.confirmed()), len(run.findings))

    def _finish_run(self, run: ReviewRun, message: str = "Review finished") -> ReviewRun:
        run.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        self._observer.run_finished(run, message)
        return run

    # ------------------------------------------------------------------ one item

    async def _review_one_item(self, item: ChecklistItem) -> ItemResult:
        self._observer.item_started(item.id, item.title)
        started = time.monotonic()
        result = ItemResult(item_id=item.id, item_title=item.title, status="running")

        request = AgentRequest(
            system=self._prompts.system_for(item),
            prompt=self._prompts.build_item_prompt(item, self._changes),
            tools=self._tools,
            terminal_tool=SUBMIT_FINDINGS_TOOL,
            settings=self._cfg.reviewer,
            metadata={"item_id": item.id},
            observer=self._observer.agent("item", item.title, item.id),
        )
        outcome = await self._runner.run(request)
        result.usage = outcome.usage
        result.turns = outcome.turns
        result.duration_s = time.monotonic() - started

        if not outcome.ok or outcome.payload is None:
            result.status = "failed"
            result.error = outcome.error or "the agent returned no result"
            self._observer.failed(f"{item.title}: {result.error}")
            return result

        result.summary, result.findings = _findings_from_payload(outcome.payload, item.id)
        result.status = "truncated" if outcome.truncated else "ok"
        return result


def merge_findings(results: list[ItemResult]) -> list[Finding]:
    """Collapses duplicates: different checklist items often flag the same line.

    Two findings are one when they sit within `MERGE_WINDOW` lines of each other
    and name the same code. Identifiers decide that, not prose: agents describe
    one defect in words too unalike to match, and two defects on one line in
    words too alike to separate.
    """
    merged: list[Finding] = []
    for group in _by_file(_reported(results)).values():
        merged.extend(_collapse(group))

    merged.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0))
    for index, finding in enumerate(merged, start=1):
        finding.id = f"F{index:03d}"
    return merged


def output_dir_for(cfg: Config, root: Path, run_id: str) -> Path:
    base = Path(cfg.run.output_dir).expanduser()
    if not base.is_absolute():
        return root / base / run_id
    # A shared external report directory may serve several repositories, so nest by
    # repo name: otherwise runs would interleave and the latest symlinks would fight
    return base / root.name / run_id


def _reported(results: list[ItemResult]) -> list[Finding]:
    """Everything worth carrying forward, each finding knowing who found it."""
    flat: list[Finding] = []
    for result in results:
        for finding in result.findings:
            finding.sources = finding.sources or [result.item_id]
            flat.append(finding)
    return flat


def _by_file(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Grouped by file, which is the only place two findings can be one.

    The window inside a file is applied when comparing, not here: bucketing by
    `line // 5` put a boundary between lines 870 and 871, so adjacent findings
    were never even compared.
    """
    groups: dict[str, list[Finding]] = {}
    for finding in findings:
        groups.setdefault(finding.file, []).append(finding)
    return groups


def _collapse(group: list[Finding]) -> list[Finding]:
    """One place, several wordings: keep one of each problem, the strongest."""
    kept: list[Finding] = []
    for finding in sorted(group, key=lambda f: (f.line is None, f.line or 0)):
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
    """The same problem already kept, in other words."""
    return next((k for k in kept if _near(k, finding) and _same_problem(k, finding)), None)


def _near(a: Finding, b: Finding) -> bool:
    """Within the window. A finding about a whole file has no line to compare,
    so it only ever meets other file-level findings."""
    if a.line is None or b.line is None:
        return a.line is None and b.line is None
    return abs(a.line - b.line) <= MERGE_WINDOW


def _same_problem(a: Finding, b: Finding) -> bool:
    """Identifiers first: naming the same symbols is what separates one defect
    written up twice from two defects on one line. Wording decides only when one
    of the two names no code at all.
    """
    ids_a, ids_b = _identifiers(a), _identifiers(b)
    if ids_a and ids_b:
        return len(ids_a & ids_b) / min(len(ids_a), len(ids_b)) >= SUBJECT_OVERLAP
    return (
        _similar(a.title, b.title) > 0.7 or _similar(a.rationale[:240], b.rationale[:240]) > 0.8
    )


def _identifiers(finding: Finding) -> set[str]:
    """Code names in the text: snake_case, camelCase, or followed by a call
    parenthesis. A plain capitalised word is not one — "The" and "Nested" open
    sentences, and matching on those merges unrelated findings.
    """
    text = f"{finding.title} {finding.rationale}"
    names: set[str] = set()
    for match in _WORD.finditer(text):
        word = match.group(0)
        called = text[match.end() : match.end() + 1] == "("
        if "_" in word.strip("_") or _CAMEL.match(word) or called:
            names.add(word)
    return names


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
