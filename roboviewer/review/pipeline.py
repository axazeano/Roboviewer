"""Orchestration: fan out over checklist items → merge → scope → judge.

What each stage does is elsewhere — the agents in `provider`, the merge in
`merge`, the verdicts in `judge`. This is the order they run in and what is
carried between them.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

from ..config import Config
from ..models import SEVERITY_ORDER, Finding, ItemResult, ReviewRun, Verdict
from ..observer import SILENT, RunObserver
from ..provider import AgentRequest, Runner
from ..repo import ChangeSet
from .checklist import ChecklistItem
from .judge import JudgeContext, JudgeSettings, judge_for
from .merge import merge_findings
from .prompts import Prompts
from .prompts.tool_schemas import SUBMIT_FINDINGS_TOOL, tool_schemas
from .prompts.turns import TURN_NOTES
from .scope import in_scope
from .submissions import findings_from_payload


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
            notes=TURN_NOTES,
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

        result.summary, result.findings = findings_from_payload(outcome.payload, item.id)
        result.status = "truncated" if outcome.truncated else "ok"
        return result


def output_dir_for(cfg: Config, root: Path, run_id: str) -> Path:
    base = Path(cfg.run.output_dir).expanduser()
    if not base.is_absolute():
        return root / base / run_id
    # A shared external report directory may serve several repositories, so nest by
    # repo name: otherwise runs would interleave and the latest symlinks would fight
    return base / root.name / run_id
