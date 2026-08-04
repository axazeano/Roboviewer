"""CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import gitdiff, renders
from .checklist import load_checklist
from .config import Config, load_config, templates_dir_for
from .models import SEVERITY_LABEL, ReviewRun
from .pipeline import Event, ReviewPipeline, output_dir_for
from .prompts import (
    DEFAULT_DIR as PROMPTS_DEFAULT_DIR,
    PromptError,
    Prompts,
    language_name,
)
from .report import save
from .runners import OpenAIAgentRunner


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


def _resolve_checklist_dir(cfg: Config, root: Path) -> Path:
    candidate = Path(cfg.run.checklist_dir).expanduser()
    if candidate.is_absolute():
        return candidate
    package_dir = Path(__file__).resolve().parent
    # A checklist inside the repository wins over the built-in one
    for base in (root, Path.cwd(), package_dir, package_dir.parent):
        resolved = base / candidate
        if resolved.is_dir():
            return resolved
    return root / candidate


def _resolve_prompts_dir(cfg: Config, root: Path) -> Path | None:
    """An explicit `prompts_dir` wins; otherwise a set living inside the reviewed
    repository is picked up on its own. None means the bundled texts."""
    if cfg.run.prompts_dir:
        candidate = Path(cfg.run.prompts_dir).expanduser()
        return candidate if candidate.is_absolute() else root / candidate
    in_repo = root / ".roboviewer" / "prompts"
    return in_repo if in_repo.is_dir() else None


def _load_prompts(cfg: Config, root: Path) -> Prompts:
    directory = _resolve_prompts_dir(cfg, root)
    # A configured directory that does not exist is a typo, not a request for the
    # defaults: the loader would fall back file by file and the run would quietly
    # go out with prompts nobody chose.
    if cfg.run.prompts_dir and (directory is None or not directory.is_dir()):
        raise PromptError(f"Prompts directory not found: {directory}")
    return Prompts.load(directory, cfg.run.output_language)


def _print_prompt_sources(cfg: Config, root: Path) -> None:
    """Only overridden templates are named — listing four bundled paths every
    time buries the one line that says the run is off the default texts."""
    try:
        prompts = _load_prompts(cfg, root)
    except PromptError as exc:
        print(f"  prompts        error: {exc}")
        return

    custom = {name: src for name, src in prompts.sources.items() if Path(src).parent != PROMPTS_DEFAULT_DIR}
    if not custom:
        print("  prompts        bundled")
        return
    print(f"  prompts        {_resolve_prompts_dir(cfg, root)}, custom: {len(custom)} of {len(prompts.sources)}")
    for name, src in custom.items():
        print(f"    {name:<14} {src}")


def _thinking_label(value: bool | None) -> str:
    return {None: "model default", True: "on", False: "off"}[value]


def _print_config(cfg: Config, root: Path) -> None:
    from .config import home_config_path, repo_config_path

    print("Config layers (each one overrides the previous):")
    known = {str(home_config_path()): "home", str(repo_config_path(root)): "repository"}
    for path in cfg.sources:
        print(f"  ✓ {path}   [{known.get(path, 'explicit --config')}]")
    for path, label in known.items():
        if path not in cfg.sources:
            print(f"  · {path}   [{label}, no file]")
    if not cfg.sources:
        print("  (no files at all — everything on defaults)")

    _, key_source = cfg.provider.api_key_source()
    print()
    print("Provider:")
    print(f"  base_url     {cfg.provider.base_url}")
    print(f"  model        {cfg.provider.model}")
    print(f"  judge_model  {cfg.provider.resolve_judge_model()}")
    print(f"  reasoning    items: {_thinking_label(cfg.provider.enable_thinking)}, "
          f"judge: {_thinking_label(cfg.provider.resolve_judge_enable_thinking())}")
    print(f"  key          {cfg.provider.masked_key()}")
    print(f"  source       {key_source}")
    print()
    print("Run:")
    print(f"  checklist_dir  {_resolve_checklist_dir(cfg, root)}")
    _print_prompt_sources(cfg, root)
    templates_dir = templates_dir_for(cfg, root)
    print(f"  templates      {templates_dir or 'bundled'}")
    print(f"  reports        {', '.join(cfg.run.report_formats)}")
    print(f"  output lang    {language_name(cfg.run.output_language) or 'not set (model answers in the prompt language)'}")
    print(f"  output_dir     {cfg.run.output_dir}")
    print(f"  concurrency    {cfg.run.concurrency}")
    print(f"  max_turns      {cfg.run.max_turns}")
    print(f"  reference pass {'on' if cfg.run.resolve_references else 'off'}")
    scope_state = f"±{cfg.run.scope_margin} lines" if cfg.run.enforce_scope else "off"
    print(f"  scope gate     {scope_state}")
    judge_state = "off" if not cfg.run.enable_judge else cfg.run.judge_mode.replace("_", "-")
    print(f"  judge          {judge_state}")
    print(f"  judge_turns    {cfg.run.resolve_judge_max_turns()}"
          f"{' (follows max_turns)' if not cfg.run.judge_max_turns else ''}")


def _print_event(event: Event, verbose: bool = False) -> None:
    if event.kind == "item_progress":
        if verbose:
            print(f"    {event.item_id or '-'} · {event.message}", flush=True)
        return
    prefix = {"error": "✗", "item_done": "•", "run_done": "✔"}.get(event.kind, "▸")
    line = f"{prefix} {event.message}"
    if event.kind == "item_done":
        result = event.data["result"]
        line += f" · {result.usage.total_tokens} tokens · {result.duration_s:.0f}s"
    print(line, flush=True)


def _print_summary(run: ReviewRun, reports: list[Path], reports_dir: Path) -> None:
    print()
    confirmed = run.confirmed()
    for finding in confirmed:
        print(f"  {finding.id}  [{SEVERITY_LABEL[finding.severity]}] {finding.location} — {finding.title}")
    if not confirmed:
        print("  No findings.")
    print()
    usage = run.total_usage
    cache = f" · {usage.cache_hit_rate:.0%} from cache" if usage.cached_tokens else " · no cache hits"
    print(f"Confirmed {len(confirmed)} of {len(run.findings)} · "
          f"{usage.total_tokens} tokens{cache}")

    cut_off = [i for i in run.items if i.status == "truncated"]
    if cut_off:
        # Worth a line of its own: these aspects reported little because they ran
        # out of turns, which reads exactly like "nothing to report" otherwise.
        print(f"⚠ Cut off by the turn limit: {', '.join(i.item_title for i in cut_off)}")
    if reports:
        print(f"Report: {', '.join(str(p) for p in reports)}")
    else:
        # report_formats = [] in the config; the machine-readable data is written anyway
        print(f"No reports requested; run data: {reports_dir}")


async def _run_review(
    cfg: Config,
    diff: gitdiff.DiffBundle,
    items: list,
    runner,
    prompts: Prompts,
    verbose: bool,
) -> int:
    origin = cfg.provider.base_url.split("//", 1)[-1].split("/", 1)[0]
    print(f"▸ {cfg.provider.model} @ {origin} · config files picked up: {len(cfg.sources)}")
    pipeline = ReviewPipeline(
        cfg, diff, items, runner, lambda event: _print_event(event, verbose), prompts
    )
    try:
        run = await pipeline.execute()
    finally:
        await runner.aclose()

    directory = output_dir_for(cfg, diff.root, run.run_id)
    try:
        reports = save(run, directory, cfg.run.report_formats, templates_dir_for(cfg, diff.root))
    except renders.RenderError as exc:
        # The run itself is already on disk; only the readable part is missing
        print(f"Report failed to render: {exc}", file=sys.stderr)
        print(f"Run data saved: {directory}", file=sys.stderr)
        return 2
    _print_summary(run, reports, directory)
    return 1 if any(i.status == "failed" for i in run.items) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Diagnostic commands work without branches and outside a repository
    informational = args.list_items or args.show_config or args.check_provider

    if not args.target and not informational:
        parser.error("no target branch given: roboviewer <target> [source]")

    requested = Path(args.repo).expanduser().resolve()
    try:
        root = gitdiff.repo_root(requested)
    except gitdiff.GitError as exc:
        if not informational:
            print(f"Error: {exc}", file=sys.stderr)
            print(
                "Point at a repository with -C PATH or the ROBOVIEWER_REPO variable.",
                file=sys.stderr,
            )
            return 2
        root = requested

    try:
        cfg = _apply_overrides(load_config(root, args.config), args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.show_config:
        _print_config(cfg, root)
        return 0

    if args.check_provider:
        from .diagnose import check_provider

        return check_provider(cfg.provider)

    try:
        items = load_checklist(
            _resolve_checklist_dir(cfg, root),
            [s for s in args.only.split(",")] if args.only else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Checklist error: {exc}", file=sys.stderr)
        return 2

    if args.list_items:
        for item in items:
            print(f"{item.id:<20} {item.title}")
        return 0

    try:
        diff = gitdiff.collect(
            root,
            args.target,
            args.source,
            context_lines=cfg.run.diff_context_lines,
            max_chars=cfg.run.diff_max_chars,
            excludes=cfg.run.exclude_globs,
            inline_max_lines=cfg.run.inline_max_lines,
            inline_max_total_chars=cfg.run.inline_max_total_chars,
            resolve_references=cfg.run.resolve_references,
        )
    except gitdiff.GitError as exc:
        print(f"Git error: {exc}", file=sys.stderr)
        return 2

    if args.diff_only:
        print(f"{diff.branch} → {diff.target} (merge-base {diff.base_sha[:12]})")
        print(diff.summary_table())
        return 0

    if not diff.files:
        print(f"No changes in {diff.branch} relative to {diff.target}.")
        return 0

    if diff.detached:
        print(f"Reviewing branch {diff.branch} ({diff.head[:12]}); the working copy is untouched.")

    # Before the runner, so a broken template costs a second rather than a
    # provider connection and eight agents failing one by one
    try:
        prompts = _load_prompts(cfg, root)
        prompts.validate(items, diff)
    except PromptError as exc:
        print(f"Prompt error: {exc}", file=sys.stderr)
        return 2

    # Same reason: a misspelled format must not surface after the tokens are
    # spent, when there is nowhere left to write the report
    try:
        renders.prepare(cfg.run.report_formats, templates_dir_for(cfg, root))
    except renders.RenderError as exc:
        print(f"Report error: {exc}", file=sys.stderr)
        return 2

    try:
        runner = OpenAIAgentRunner(cfg.provider, cfg.run, root, diff.base_sha, diff.source_ref)
    except RuntimeError as exc:
        print(f"Provider error: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(_run_review(cfg, diff, items, runner, prompts, args.verbose))


if __name__ == "__main__":
    raise SystemExit(main())
