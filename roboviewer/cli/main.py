"""The command: the order the steps run in, and what a failure exits with.

Each step below either produces its part of the run or raises `CLIError`, so
`main` has one place that prints a failure and one that decides the exit code.
The flags are `arguments`, what the steps produce is printed by `console`, and
where their files come from is decided by `config.overrides`.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from .. import repo
from ..config import Config, load_config, overrides
from ..observer import SILENT, Broadcast, RunObserver
from ..provider import OpenAIAgentRunner, Runner
from ..reports import renders, save
from ..review import (
    ChecklistItem,
    PromptError,
    Prompts,
    ReviewPipeline,
    load_checklist,
    output_dir_for,
)
from . import ci_env, console, exit_codes
from .arguments import apply_overrides, build_parser


class CLIError(RuntimeError):
    """A failure the user can do something about.

    Every setup step raises this instead of printing and returning a code of its
    own — thirteen of those in one function was most of what made it unreadable.
    """

    def __init__(self, message: str, hint: str = "", code: int = exit_codes.SETUP) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


@dataclass
class RunPlan:
    """Everything the review needs, with every failure already surfaced."""

    cfg: Config
    changes: repo.ChangeSet
    items: list[ChecklistItem]
    prompts: Prompts
    runner: Runner
    templates_dir: Path | None


def main(argv: list[str] | None = None, observer: RunObserver = SILENT) -> int:
    """The command. `observer` is how something outside the tool asks to be
    told what the run and its agents did — see `observer`; by default nobody is
    watching and the run keeps no account of itself."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _execute(args, parser, observer)
    except CLIError as exc:
        console.error(exc.message, exc.hint)
        return exc.code


def _execute(
    args: argparse.Namespace, parser: argparse.ArgumentParser, observer: RunObserver
) -> int:
    """The order the steps run in, and where each of them can stop the run early.

    Everything that fails raises `CLIError`; what stops on purpose returns a code
    of its own, because `--diff-only` finishing is not a failure.
    """
    # Diagnostic commands work without branches and outside a repository
    informational = args.list_items or args.show_config or args.check_provider
    root = _repo_root(args.repo, required=not informational)
    cfg = apply_overrides(_config(args.config), args)

    if args.show_config:
        console.config(cfg, root)
        return 0
    if args.check_provider:
        from .check_provider import check_provider

        return check_provider(cfg.provider, cfg.reviewer.model, cfg.provider_source)

    items = _checklist(cfg, root, args.only)
    if args.list_items:
        console.checklist_items(items)
        return 0

    # Every command that works without branches has returned by now
    target = args.target or _target_from_ci()
    if not target:
        parser.error("no target branch given: roboviewer <target> [source]")

    changes = _changes(cfg, root, target, args.source)
    compared = changes.comparison
    if args.diff_only:
        console.diff_summary(changes)
        return 0
    if not changes.files:
        console.notice(f"No changes in {compared.source} relative to {compared.target}.")
        return 0

    plan = _plan(cfg, root, changes, items)
    if compared.detached:
        console.notice(
            f"Reviewing branch {compared.source} ({compared.head_sha[:12]}); "
            "the working copy is untouched."
        )
    return asyncio.run(_review(plan, args.verbose, observer))


def _target_from_ci() -> str | None:
    """The target branch a merge-request pipeline already names in a variable.

    Announced rather than assumed: a review is against a branch, and which one
    should be readable in the job log without knowing the runner's variables.
    """
    environment = ci_env.detect()
    if environment is None:
        return None
    console.notice(
        f"Target branch from {environment.name}: "
        f"{environment.target} (${environment.variable})"
    )
    return environment.target


def _repo_root(requested: str, *, required: bool) -> Path:
    path = Path(requested).expanduser().resolve()
    try:
        return repo.repo_root(path)
    except repo.GitError as exc:
        if required:
            raise CLIError(
                f"Error: {exc}",
                "Point at a repository with -C PATH or the ROBOVIEWER_REPO variable.",
            ) from exc
        # A diagnostic command has no repository to work on and does not need one
        return path


def _config(explicit: Path | None) -> Config:
    try:
        return load_config(explicit)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(f"Config error: {exc}") from exc


def _checklist(cfg: Config, root: Path, only: str | None) -> list[ChecklistItem]:
    try:
        return load_checklist(
            overrides.checklist_dir(cfg, root),
            only.split(",") if only else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(f"Checklist error: {exc}") from exc


def _changes(cfg: Config, root: Path, target: str, source: str | None) -> repo.ChangeSet:
    try:
        return repo.collect(
            root,
            target,
            source,
            budget=repo.ContextBudget(
                context_lines=cfg.run.diff_context_lines,
                max_chars=cfg.run.diff_max_chars,
                inline_max_lines=cfg.run.inline_max_lines,
                inline_max_total_chars=cfg.run.inline_max_total_chars,
            ),
            excludes=cfg.run.exclude_globs,
            resolve_references=cfg.run.resolve_references,
        )
    except repo.GitError as exc:
        raise CLIError(f"Git error: {exc}", _depth_hint(root)) from exc


def _depth_hint(root: Path) -> str:
    """Both runners clone shallow by default, and every git failure that causes
    looks like something else — a branch that does not exist, a diff with no
    branch point. Say it once, where the failure surfaces."""
    try:
        if not repo.is_shallow(root):
            return ""
    except repo.GitError:
        return ""
    return (
        "This clone is shallow, so the branch point may simply be missing. "
        "Deepen it (git fetch --unshallow, or GIT_DEPTH: 0 in .gitlab-ci.yml, "
        "or fetch-depth: 0 for actions/checkout)."
    )


def _plan(
    cfg: Config, root: Path, changes: repo.ChangeSet, items: list[ChecklistItem]
) -> RunPlan:
    """Everything that can still fail, gathered before the first request.

    A broken prompt template, a misspelled format or a missing key costs a second
    here; found later it costs the whole token bill, with nowhere left to write
    the report.
    """
    templates = overrides.templates_dir(cfg, root)
    try:
        prompts = Prompts.for_run(cfg, root)
        prompts.validate(items, changes)
    except PromptError as exc:
        raise CLIError(f"Prompt error: {exc}") from exc

    try:
        renders.prepare(cfg.run.report_formats, templates)
    except renders.RenderError as exc:
        raise CLIError(f"Report error: {exc}") from exc

    try:
        runner = OpenAIAgentRunner(
            cfg.provider, cfg.run, root, changes.comparison.base_sha, changes.comparison.source_ref
        )
    except RuntimeError as exc:
        raise CLIError(f"Provider error: {exc}") from exc

    return RunPlan(
        cfg=cfg,
        changes=changes,
        items=items,
        prompts=prompts,
        runner=runner,
        templates_dir=templates,
    )


async def _review(plan: RunPlan, verbose: bool, observer: RunObserver = SILENT) -> int:
    console.run_header(plan.cfg)
    pipeline = ReviewPipeline(
        plan.cfg,
        plan.changes,
        plan.items,
        plan.runner,
        plan.prompts,
        observer=Broadcast([console.Console(verbose), observer]),
    )
    try:
        run = await pipeline.execute()
    finally:
        await plan.runner.aclose()

    directory = output_dir_for(plan.cfg, plan.changes.comparison.root, run.run_id)
    try:
        reports = save(run, directory, plan.cfg.run.report_formats, plan.templates_dir)
    except renders.RenderError as exc:
        # The run itself is already on disk; only the readable part is missing
        console.error(f"Report failed to render: {exc}", f"Run data saved: {directory}")
        return exit_codes.SETUP
    console.summary(run, reports, directory)
    threshold = plan.cfg.run.fail_on
    console.gate_result(exit_codes.blocking(run, threshold), threshold)
    return exit_codes.exit_code(run, threshold)


if __name__ == "__main__":
    raise SystemExit(main())
