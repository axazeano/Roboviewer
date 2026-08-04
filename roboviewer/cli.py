"""CLI entry point: the order the steps run in, and what a failure exits with.

Each step below either produces its part of the run or raises `CLIError`, so
`main` has one place that prints a failure and one that decides the exit code.
What the steps produce is printed by `console`, and where their files come from
is decided by `sources`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from . import console, gitdiff, renders, sources
from .checklist import ChecklistItem, load_checklist
from .config import Config, load_config
from .pipeline import ReviewPipeline, output_dir_for
from .prompts import PromptError, Prompts
from .report import save
from .runners import OpenAIAgentRunner, Runner


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
        help="Path to the repository under review (defaults to $ROBOVIEWER_REPO or the current directory)",
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
    parser.add_argument("--config", type=Path, help="Explicit path to config.toml")
    parser.add_argument("--checklist", help="Directory holding the checklist items")
    parser.add_argument("--only", help="Run only these items, comma-separated")
    parser.add_argument("--model", help="Override the model")
    parser.add_argument(
        "--thinking",
        choices=("on", "off"),
        help=(
            "Reasoning mode for this run, for both the items and the judge. "
            "Without the flag, whatever the config says"
        ),
    )
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
    parser.add_argument("-j", "--concurrency", type=int, help="How many items to review in parallel")
    parser.add_argument("--no-judge", action="store_true", help="Skip the final judge pass")
    parser.add_argument(
        "--judge-mode",
        choices=("batch", "per-finding", "two-stage"),
        help=(
            "How findings get verified: 'batch' is one pass over the whole list, "
            "'per-finding' spends a separate pass on each, 'two-stage' does that "
            "and then rules on the survivors together. Without the flag, "
            "whatever the config says"
        ),
    )
    parser.add_argument(
        "--judge-turns",
        type=int,
        metavar="N",
        help=(
            "Turn budget for one judging pass. Without the flag it follows "
            "max_turns — worth lowering with --judge-mode per-finding, where a "
            "pass has a single claim to settle"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream agent activity: tool calls, retries, errors",
    )
    parser.add_argument("--list-items", action="store_true", help="Print the checklist items and exit")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print which config files were picked up and the result of stacking them",
    )
    parser.add_argument(
        "--check-provider",
        action="store_true",
        help="Make one probe request to the provider and break down the answer (for debugging 401 and friends)",
    )
    parser.add_argument("--diff-only", action="store_true", help="Print the diff summary and exit")
    return parser


class CLIError(RuntimeError):
    """A failure the user can do something about.

    Every setup step raises this instead of printing and returning a code of its
    own — thirteen of those in one function was most of what made it unreadable.
    """

    def __init__(self, message: str, hint: str = "", code: int = 2) -> None:
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _execute(args, parser)
    except CLIError as exc:
        console.error(exc.message, exc.hint)
        return exc.code


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    if args.output:
        cfg.run.output_dir = str(Path(args.output).expanduser())
    if args.checklist:
        cfg.run.checklist_dir = args.checklist
    if args.model:
        cfg.provider.model = args.model
    if args.format:
        # Replaces the configured list rather than extending it: --format md is a
        # way to say "just markdown this time", and appending would make that
        # impossible.
        cfg.run.report_formats = args.format
    if args.thinking:
        # Overrides both stages; telling them apart is a config-level choice
        cfg.provider.enable_thinking = args.thinking == "on"
        cfg.provider.judge_enable_thinking = cfg.provider.enable_thinking
    if args.language:
        cfg.run.output_language = args.language
    if args.concurrency:
        cfg.run.concurrency = args.concurrency
    if args.judge_mode:
        # Hyphen on the command line, underscore in the config
        cfg.run.judge_mode = args.judge_mode.replace("-", "_")
    if args.judge_turns:
        cfg.run.judge_max_turns = args.judge_turns
    if args.no_judge:
        cfg.run.enable_judge = False
    return cfg


def _execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """The order the steps run in, and where each of them can stop the run early.

    Everything that fails raises `CLIError`; what stops on purpose returns a code
    of its own, because `--diff-only` finishing is not a failure.
    """
    # Diagnostic commands work without branches and outside a repository
    informational = args.list_items or args.show_config or args.check_provider
    if not args.target and not informational:
        parser.error("no target branch given: roboviewer <target> [source]")

    root = _repo_root(args.repo, required=not informational)
    cfg = _apply_overrides(_config(root, args.config), args)

    if args.show_config:
        console.config(cfg, root)
        return 0
    if args.check_provider:
        from .diagnose import check_provider

        return check_provider(cfg.provider)

    items = _checklist(cfg, root, args.only)
    if args.list_items:
        console.checklist_items(items)
        return 0

    diff = _diff(cfg, root, args.target, args.source)
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
    return asyncio.run(_review(plan, args.verbose))


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


def _config(root: Path, explicit: Path | None) -> Config:
    try:
        return load_config(root, explicit)
    except (FileNotFoundError, ValueError) as exc:
        raise CLIError(f"Config error: {exc}") from exc


def _checklist(cfg: Config, root: Path, only: str | None) -> list[ChecklistItem]:
    try:
        return load_checklist(
            sources.checklist_dir(cfg, root),
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
            context_lines=cfg.run.diff_context_lines,
            max_chars=cfg.run.diff_max_chars,
            excludes=cfg.run.exclude_globs,
            inline_max_lines=cfg.run.inline_max_lines,
            inline_max_total_chars=cfg.run.inline_max_total_chars,
            resolve_references=cfg.run.resolve_references,
        )
    except gitdiff.GitError as exc:
        raise CLIError(f"Git error: {exc}") from exc


def _plan(
    cfg: Config, root: Path, diff: gitdiff.DiffBundle, items: list[ChecklistItem]
) -> RunPlan:
    """Everything that can still fail, gathered before the first request.

    A broken prompt template, a misspelled format or a missing key costs a second
    here; found later it costs the whole token bill, with nowhere left to write
    the report.
    """
    templates = sources.templates_dir(cfg, root)
    try:
        prompts = sources.load_prompts(cfg, root)
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


async def _review(plan: RunPlan, verbose: bool) -> int:
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
        run = await pipeline.execute()
    finally:
        await plan.runner.aclose()

    directory = output_dir_for(plan.cfg, plan.diff.root, run.run_id)
    try:
        reports = save(run, directory, plan.cfg.run.report_formats, plan.templates_dir)
    except renders.RenderError as exc:
        # The run itself is already on disk; only the readable part is missing
        console.error(f"Report failed to render: {exc}", f"Run data saved: {directory}")
        return 2
    console.summary(run, reports, directory)
    return 1 if any(i.status == "failed" for i in run.items) else 0


if __name__ == "__main__":
    raise SystemExit(main())
