"""Everything the CLI prints.

Kept apart from `cli` for the same reason reports are kept out of `pipeline`:
deciding what a run does and describing it to a human are two jobs, and only one
of them changes when the wording does. `cli` is left with the flow and the exit
codes.

Progress goes to stdout, failures to stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import sources
from .checklist import ChecklistItem
from .config import Config, home_config_path, repo_config_path
from .events import Event
from .gitdiff import DiffBundle
from .models import SEVERITY_LABEL, ReviewRun
from .prompts import PromptError, language_name


def error(message: str, hint: str = "") -> None:
    print(message, file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)


def notice(message: str) -> None:
    print(message)


def run_header(cfg: Config) -> None:
    origin = cfg.provider.base_url.split("//", 1)[-1].split("/", 1)[0]
    print(f"▸ {cfg.provider.model} @ {origin} · config files picked up: {len(cfg.sources)}")


def event(entry: Event, verbose: bool = False) -> None:
    if entry.kind == "item_progress":
        if verbose:
            print(f"    {entry.item_id or '-'} · {entry.message}", flush=True)
        return
    prefix = {"error": "✗", "item_done": "•", "run_done": "✔"}.get(entry.kind, "▸")
    line = f"{prefix} {entry.message}"
    if entry.kind == "item_done":
        result = entry.data["result"]
        line += f" · {result.usage.total_tokens} tokens · {result.duration_s:.0f}s"
    print(line, flush=True)


def summary(run: ReviewRun, reports: list[Path], reports_dir: Path) -> None:
    print()
    confirmed = run.confirmed()
    for finding in confirmed:
        print(
            f"  {finding.id}  [{SEVERITY_LABEL[finding.severity]}] "
            f"{finding.location} — {finding.title}"
        )
    if not confirmed:
        print("  No findings.")
    print()
    usage = run.total_usage
    cache = (
        f" · {usage.cache_hit_rate:.0%} from cache" if usage.cached_tokens else " · no cache hits"
    )
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


def checklist_items(items: list[ChecklistItem]) -> None:
    for item in items:
        print(f"{item.id:<20} {item.title}")


def diff_summary(diff: DiffBundle) -> None:
    print(f"{diff.branch} → {diff.target} (merge-base {diff.base_sha[:12]})")
    print(diff.summary_table())


def config(cfg: Config, root: Path) -> None:
    _config_layers(cfg, root)
    print()
    _provider(cfg)
    print()
    _run(cfg, root)


def _config_layers(cfg: Config, root: Path) -> None:
    print("Config layers (each one overrides the previous):")
    known = {str(home_config_path()): "home", str(repo_config_path(root)): "repository"}
    for path in cfg.sources:
        print(f"  ✓ {path}   [{known.get(path, 'explicit --config')}]")
    for path, label in known.items():
        if path not in cfg.sources:
            print(f"  · {path}   [{label}, no file]")
    if not cfg.sources:
        print("  (no files at all — everything on defaults)")


def _provider(cfg: Config) -> None:
    _, key_source = cfg.provider.api_key_source()
    print("Provider:")
    print(f"  base_url     {cfg.provider.base_url}")
    print(f"  model        {cfg.provider.model}")
    print(f"  judge_model  {cfg.provider.resolve_judge_model()}")
    print(f"  reasoning    items: {_thinking(cfg.provider.enable_thinking)}, "
          f"judge: {_thinking(cfg.provider.resolve_judge_enable_thinking())}")
    print(f"  key          {cfg.provider.masked_key()}")
    print(f"  source       {key_source}")


def _run(cfg: Config, root: Path) -> None:
    print("Run:")
    print(f"  checklist_dir  {sources.checklist_dir(cfg, root)}")
    _prompt_sources(cfg, root)
    print(f"  templates      {sources.templates_dir(cfg, root) or 'bundled'}")
    print(f"  reports        {', '.join(cfg.run.report_formats)}")
    language = language_name(cfg.run.output_language)
    print(f"  output lang    {language or 'not set (model answers in the prompt language)'}")
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


def _prompt_sources(cfg: Config, root: Path) -> None:
    """Only overridden templates are named — listing four bundled paths every
    time buries the one line that says the run is off the default texts."""
    try:
        prompts = sources.load_prompts(cfg, root)
    except PromptError as exc:
        print(f"  prompts        error: {exc}")
        return

    custom = sources.custom_prompts(prompts)
    if not custom:
        print("  prompts        bundled")
        return
    print(f"  prompts        {sources.prompts_dir(cfg, root)}, "
          f"custom: {len(custom)} of {len(prompts.sources)}")
    for name, source in custom.items():
        print(f"    {name:<14} {source}")


def _thinking(value: bool | None) -> str:
    return {None: "model default", True: "on", False: "off"}[value]
