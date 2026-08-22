"""CLI entry point: the order the steps run in, and what a failure exits with.

Each step below either produces its part of the run or raises `CLIError`, so
`main` has one place that prints a failure and one that decides the exit code.
What the steps produce is printed by `console`, and where their files come from
is decided by `config.overrides`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from . import ci, console, gate, gitdiff, renders
from .checklist import ChecklistItem, load_checklist
from .config import Config, load_config, overrides
from .observe import SILENT, RunObserver
from .pipeline import ReviewPipeline, output_dir_for
from .prompts import PromptError, Prompts
from .provider import OpenAIAgentRunner, Runner
from .report import save


def report_formats(value: str) -> list[str]:
    """`md,html` → a list of formats. Whether a format is known is checked
    later, once the config is parsed and the templates directory is known."""
    formats = [fmt.strip() for fmt in value.split(",") if fmt.strip()]
    if not formats:
        raise argparse.ArgumentTypeError(
            f"list formats separated by commas, e.g. {','.join(renders.known())}"
        )
    return formats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roboviewer",
        description="A local, agent-driven automated reviewer for merge requests.",
        epilog=(
            "Examples:\n"
            "  roboviewer develop                     review the current branch into develop\n"
            "  roboviewer develop feature/login       review the named branch, no checkout needed\n"
            "  roboviewer release/2.0 develop         develop into a release branch\n"
            "  roboviewer -C ~/work/app develop       a repository living elsewhere\n"
            "\n"
            "Environment variables:\n"
            "  ROBOVIEWER_REPO    default repository (when -C is not given)\n"
            "  ROBOVIEWER_OUTPUT  where reports go (when --output is not given)\n"
            "  ROBOVIEWER_PROVIDER_CONFIG  the provider file, for a runner or a\n"
            "                     container with no ~/.config/roboviewer/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Target branch — what we merge into (required)",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Source branch — what we merge (defaults to the current one)",
    )
    parser.add_argument(
        "-C",
        "--repo",
        default=os.environ.get("ROBOVIEWER_REPO", "."),
        metavar="PATH",
        help=(
            "Path to the repository under review "
            "(defaults to $ROBOVIEWER_REPO or the current directory)"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=os.environ.get("ROBOVIEWER_OUTPUT"),
        metavar="PATH",
        help=(
            "Where reports go. Defaults to .roboviewer/runs inside the repository under "
            "review; point it outside to keep the working tree clean"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Read this file instead of ~/.config/roboviewer/config.toml. It "
            "replaces that file rather than adding to it, and carries "
            "[reviewer], [judge] and [run] — the provider is read from "
            "~/.config/roboviewer/provider.toml either way, so a file passed "
            "here can be shared without carrying a key"
        ),
    )
    parser.add_argument("--checklist", help="Directory holding the checklist items")
    parser.add_argument("--only", help="Run only these items, comma-separated")
    parser.add_argument(
        "--format",
        type=report_formats,
        metavar="LIST",
        help=(
            "Report formats, comma-separated: md, html, sarif, codequality. "
            "Replaces report_formats from the config entirely; without the flag, as set there"
        ),
    )
    parser.add_argument(
        "--language",
        metavar="LANG",
        help=(
            "Language for the model's own text: finding titles, rationales, "
            "suggestions, the judge's summary. Takes an ISO code or a name — "
            "ru, Russian, German. Without the flag, whatever the config says"
        ),
    )
    parser.add_argument(
        "--fail-on",
        choices=gate.THRESHOLDS,
        metavar="SEVERITY",
        help=(
            "Exit 1 when a confirmed finding of this severity or worse is left "
            f"standing, so a CI job goes red on it: {', '.join(gate.THRESHOLDS)}. "
            "Without the flag, whatever the config says"
        ),
    )
    parser.add_argument(
        "-j", "--concurrency", type=int, help="How many items to review in parallel"
    )
    parser.add_argument("--no-judge", action="store_true", help="Skip the final judge pass")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream agent activity: tool calls, retries, errors",
    )
    parser.add_argument(
        "--list-items", action="store_true", help="Print the checklist items and exit"
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print the settings this run would use, and the file they came from",
    )
    parser.add_argument(
        "--check-provider",
        action="store_true",
        help=(
            "Make one probe request to the provider and break down the answer "
            "(for debugging 401 and friends)"
        ),
    )
    parser.add_argument("--diff-only", action="store_true", help="Print the diff summary and exit")
    return parser


class CLIError(RuntimeError):
    """A failure the user can do something about.

    Every setup step raises this instead of printing and returning a code of its
    own — thirteen of those in one function was most of what made it unreadable.
    """

    def __init__(self, message: str, hint: str = "", code: int = gate.SETUP) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


@dataclass
class RunPlan:
    """Everything the review needs, with every failure already surfaced."""

    cfg: Config
    diff: gitdiff.DiffBundle
    items: list[ChecklistItem]
    prompts: Prompts
    runner: Runner
    templates_dir: Path | None


def main(argv: list[str] | None = None, observer: RunObserver = SILENT) -> int:
    """The command. `observer` is how something outside the tool asks to be
    told what the agents did — see `observe`; by default nobody is watching and
    the run keeps no account of itself."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _execute(args, parser, observer)
    except CLIError as exc:
        console.error(exc.message, exc.hint)
        return exc.code


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    if args.output:
        cfg.run.output_dir = str(Path(args.output).expanduser())
    if args.checklist:
        cfg.run.checklist_dir = args.checklist
    if args.format:
        # Replaces the configured list rather than extending it: --format md is a
        # way to say "just markdown this time", and appending would make that
        # impossible.
        cfg.run.report_formats = args.format
    if args.language:
        cfg.run.output_language = args.language
    if args.concurrency:
        cfg.run.concurrency = args.concurrency
    if args.fail_on:
        cfg.run.fail_on = args.fail_on
    if args.no_judge:
        cfg.run.enable_judge = False
    return cfg


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
    cfg = _apply_overrides(_config(args.config), args)

    if args.show_config:
        console.config(cfg, root)
        return 0
    if args.check_provider:
        from .diagnose import check_provider

        return check_provider(cfg.provider, cfg.reviewer.model, cfg.provider_source)

    items = _checklist(cfg, root, args.only)
    if args.list_items:
        console.checklist_items(items)
        return 0

    # Every command that works without branches has returned by now
    target = args.target or _target_from_ci()
    if not target:
        parser.error("no target branch given: roboviewer <target> [source]")

    diff = _diff(cfg, root, target, args.source)
    if args.diff_only:
        console.diff_summary(diff)
        return 0
    if not diff.files:
        console.notice(f"No changes in {diff.branch} relative to {diff.target}.")
        return 0

    plan = _plan(cfg, root, diff, items)
    if diff.detached:
        console.notice(
            f"Reviewing branch {diff.branch} ({diff.head[:12]}); the working copy is untouched."
        )
    return asyncio.run(_review(plan, args.verbose, observer))


def _target_from_ci() -> str | None:
    """The target branch a merge-request pipeline already names in a variable.

    Announced rather than assumed: a review is against a branch, and which one
    should be readable in the job log without knowing the runner's variables.
    """
    environment = ci.detect()
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
        return gitdiff.repo_root(path)
    except gitdiff.GitError as exc:
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


def _diff(cfg: Config, root: Path, target: str, source: str | None) -> gitdiff.DiffBundle:
    try:
        return gitdiff.collect(
            root,
            target,
            source,
            budget=gitdiff.DiffBudget(
                context_lines=cfg.run.diff_context_lines,
                max_chars=cfg.run.diff_max_chars,
                inline_max_lines=cfg.run.inline_max_lines,
                inline_max_total_chars=cfg.run.inline_max_total_chars,
            ),
            excludes=cfg.run.exclude_globs,
            resolve_references=cfg.run.resolve_references,
        )
    except gitdiff.GitError as exc:
        raise CLIError(f"Git error: {exc}", _depth_hint(root)) from exc


def _depth_hint(root: Path) -> str:
    """Both runners clone shallow by default, and every git failure that causes
    looks like something else — a branch that does not exist, a diff with no
    branch point. Say it once, where the failure surfaces."""
    try:
        if not gitdiff.is_shallow(root):
            return ""
    except gitdiff.GitError:
        return ""
    return (
        "This clone is shallow, so the branch point may simply be missing. "
        "Deepen it (git fetch --unshallow, or GIT_DEPTH: 0 in .gitlab-ci.yml, "
        "or fetch-depth: 0 for actions/checkout)."
    )


def _plan(
    cfg: Config, root: Path, diff: gitdiff.DiffBundle, items: list[ChecklistItem]
) -> RunPlan:
    """Everything that can still fail, gathered before the first request.

    A broken prompt template, a misspelled format or a missing key costs a second
    here; found later it costs the whole token bill, with nowhere left to write
    the report.
    """
    templates = overrides.templates_dir(cfg, root)
    try:
        prompts = Prompts.for_run(cfg, root)
        prompts.validate(items, diff)
    except PromptError as exc:
        raise CLIError(f"Prompt error: {exc}") from exc

    try:
        renders.prepare(cfg.run.report_formats, templates)
    except renders.RenderError as exc:
        raise CLIError(f"Report error: {exc}") from exc

    try:
        runner = OpenAIAgentRunner(cfg.provider, cfg.run, root, diff.base_sha, diff.source_ref)
    except RuntimeError as exc:
        raise CLIError(f"Provider error: {exc}") from exc

    return RunPlan(
        cfg=cfg,
        diff=diff,
        items=items,
        prompts=prompts,
        runner=runner,
        templates_dir=templates,
    )


async def _review(plan: RunPlan, verbose: bool, observer: RunObserver = SILENT) -> int:
    console.run_header(plan.cfg)
    pipeline = ReviewPipeline(
        plan.cfg,
        plan.diff,
        plan.items,
        plan.runner,
        lambda entry: console.event(entry, verbose),
        plan.prompts,
    )
    try:
        run = await pipeline.execute(observer)
    finally:
        await plan.runner.aclose()

    directory = output_dir_for(plan.cfg, plan.diff.root, run.run_id)
    try:
        reports = save(run, directory, plan.cfg.run.report_formats, plan.templates_dir)
    except renders.RenderError as exc:
        # The run itself is already on disk; only the readable part is missing
        console.error(f"Report failed to render: {exc}", f"Run data saved: {directory}")
        return gate.SETUP
    console.summary(run, reports, directory)
    threshold = plan.cfg.run.fail_on
    console.gate_result(gate.blocking(run, threshold), threshold)
    return gate.exit_code(run, threshold)


if __name__ == "__main__":
    raise SystemExit(main())
