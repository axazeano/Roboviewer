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

from . import gate, sources
from .checklist import ChecklistItem
from .config import Config, ModelConfig, RateLimits, home_config_path
from .events import Event
from .gitdiff import DiffBundle
from .metering import Meter
from .models import SEVERITY_LABEL, Finding, ReviewRun
from .prompts import PromptError, language_name


def error(message: str, hint: str = "") -> None:
    print(message, file=sys.stderr)
    if hint:
        print(hint, file=sys.stderr)


def notice(message: str) -> None:
    print(message)


def run_header(cfg: Config) -> None:
    origin = cfg.provider.base_url.split("//", 1)[-1].split("/", 1)[0]
    # The file, not a count: when a run goes to an endpoint nobody expected,
    # this is the line that says which file sent it there.
    print(f"▸ {cfg.reviewer.model} @ {origin} · config: {cfg.source or 'built-in defaults'}")


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


def gate_result(blocking: list[Finding], threshold: str) -> None:
    """Why the run is about to exit non-zero.

    Printed only when a gate is set: a run nobody asked to gate should not have
    a line about gating in it. The severity is named because the reader's next
    move is either to fix the finding or to lower the threshold.
    """
    if threshold == gate.NEVER:
        return
    if not blocking:
        print(f"✔ Gate: nothing at {threshold} or worse.")
        return
    print(f"✗ Gate: {len(blocking)} finding(s) at {threshold} or worse — "
          f"{', '.join(f.id for f in blocking)}")


def checklist_items(items: list[ChecklistItem]) -> None:
    for item in items:
        print(f"{item.id:<20} {item.title}")


def diff_summary(diff: DiffBundle) -> None:
    print(f"{diff.branch} → {diff.target} (merge-base {diff.base_sha[:12]})")
    print(diff.summary_table())


def config(cfg: Config, root: Path) -> None:
    _config_source(cfg)
    print()
    _provider(cfg)
    print()
    _roles(cfg)
    print()
    _run(cfg, root)


def _config_source(cfg: Config) -> None:
    print("Config:")
    if cfg.source is None:
        print(f"  · {home_config_path()}   [no file — everything on defaults]")
        return
    origin = "default location" if cfg.source == str(home_config_path()) else "--config"
    print(f"  ✓ {cfg.source}   [{origin}]")


def _provider(cfg: Config) -> None:
    _, key_source = cfg.provider.api_key_source()
    print("Provider:")
    print(f"  base_url     {cfg.provider.base_url}")
    print(f"  key          {cfg.provider.masked_key()}")
    print(f"  source       {key_source}")
    meter, why = cfg.provider.meter()
    print(f"  metering     {meter.name} — {meter.why}")
    print(f"               ({why})")
    print(f"  pacing       {_pacing(meter, cfg.provider.rate_limits)}")


def _pacing(meter: Meter, limits: RateLimits) -> str:
    """How a run will hold itself back, and against which buckets.

    Worth printing in full: a run that paces itself is slower on purpose, and
    which gateway family it decided it was talking to is the one guess in here
    that a person can correct.
    """
    if not meter.paces:
        # Only the cooldown after a refusal is left, and that needs no setting
        return "none — concurrency is the only limit, plus a hold after any 429"
    named = ", ".join(f"{name} {value}/min" for name, value in limits.per_minute.items() if value)
    source = (
        "pacing from what the gateway reports is left"
        if meter.reports_remaining and limits.adopt_advertised
        else "keeping its own count between answers"
    )
    adopting = (
        "adopting advertised ceilings" if limits.adopt_advertised else "ignoring what is advertised"
    )
    unset = f"no ceilings set for {', '.join(meter.names())}"
    return " · ".join([named or unset, source, adopting])


def _roles(cfg: Config) -> None:
    """Both roles, and whether the judge is a section of its own.

    Printed together because the question people bring here is which model each
    stage runs on, and that used to take reading two sections and a fallback.
    """
    _role("Reviewer", cfg.reviewer)
    print()
    _role("Judge", cfg.for_judge(), follows="" if cfg.judge else " (no [judge] section)")


def _role(title: str, model: ModelConfig, follows: str = "") -> None:
    print(f"{title}:{follows}")
    print(f"  model        {model.model}")
    print(f"  max_turns    {model.max_turns}")
    print(f"  reasoning    {_thinking(model.enable_thinking)}")
    print(f"  sampling     temperature {model.temperature}, max_tokens {model.max_tokens}")


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
    print(f"  reference pass {'on' if cfg.run.resolve_references else 'off'}")
    scope_state = f"±{cfg.run.scope_margin} lines" if cfg.run.enforce_scope else "off"
    print(f"  scope gate     {scope_state}")
    print(f"  fail_on        {cfg.run.fail_on}"
          f"{'' if cfg.run.fail_on == gate.NEVER else ' (exits 1 at this severity or worse)'}")
    judge_state = "off" if not cfg.run.enable_judge else cfg.run.judge_mode.replace("_", "-")
    print(f"  judge          {judge_state}")


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
