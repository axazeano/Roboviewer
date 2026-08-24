"""Everything the CLI prints.

Kept apart from `cli` for the same reason reports are kept out of `pipeline`:
deciding what a run does and describing it to a human are two jobs, and only one
of them changes when the wording does. `cli` is left with the flow and the exit
codes.

`Console` is the observer the CLI attaches to a run, printing a line per stage
as it happens; the functions below it print what the CLI has to say before and
after. Progress goes to stdout, failures to stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..config import (
    Config,
    ModelConfig,
    RateLimits,
    home_config_path,
    overrides,
    provider_config_path,
)
from ..models import SEVERITY_LABEL, Finding, ItemResult, ReviewRun
from ..observer import AgentKind, AgentObserver, Observer
from ..repo import ChangeSet
from ..review import ChecklistItem, PromptError, Prompts
from ..review.prompts import language_name
from . import exit_codes


class Console(Observer):
    """A run's progress, one line per stage, as it happens."""

    def __init__(self, verbose: bool = False) -> None:
        self._verbose = verbose

    def run_started(self, run: ReviewRun, directory: Path) -> None:  # noqa: ARG002
        _line(
            f"▸ {run.branch} → {run.target}: {len(run.files)} files, "
            f"{len(run.items)} checklist items"
        )

    def item_started(self, item_id: str, title: str) -> None:  # noqa: ARG002
        _line(f"▸ Started: {title}")

    def item_finished(self, item_id: str, title: str, result: ItemResult) -> None:  # noqa: ARG002
        _line(
            f"• {title}: {len(result.findings)} findings ({result.status})"
            f" · {result.usage.total_tokens} tokens · {result.duration_s:.0f}s"
        )

    def merged(self, count: int) -> None:
        _line(f"▸ After merge and deduplication: {count} findings")

    def out_of_scope(self, count: int) -> None:
        _line(f"▸ {count} pointed outside the changed lines and are listed separately")

    def judging(self, message: str) -> None:
        _line(f"▸ {message}")

    def judged(self, confirmed: int, total: int) -> None:
        _line(f"▸ Confirmed {confirmed} of {total}")

    def failed(self, message: str) -> None:
        _line(f"✗ {message}")

    def run_finished(self, run: ReviewRun, message: str) -> None:  # noqa: ARG002
        _line(f"✔ {message}")

    def agent(self, kind: AgentKind, title: str, item_id: str = "") -> AgentObserver:
        return _AgentLines(kind, title, item_id, verbose=self._verbose)


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
    # A provider still sharing a file with the settings is worth one line before
    # the run rather than a page in the docs nobody opens twice.
    if cfg.provider_notice:
        print(f"⚠ {cfg.provider_notice}")


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
    if threshold == exit_codes.NEVER:
        return
    if not blocking:
        print(f"✔ Gate: nothing at {threshold} or worse.")
        return
    print(f"✗ Gate: {len(blocking)} finding(s) at {threshold} or worse — "
          f"{', '.join(f.id for f in blocking)}")


def checklist_items(items: list[ChecklistItem]) -> None:
    for item in items:
        print(f"{item.id:<20} {item.title}")


def diff_summary(changes: ChangeSet) -> None:
    compared = changes.comparison
    print(f"{compared.source} → {compared.target} (merge-base {compared.base_sha[:12]})")
    print(changes.summary_table())


def config(cfg: Config, root: Path) -> None:
    _config_source(cfg)
    print()
    _provider(cfg)
    print()
    _roles(cfg)
    print()
    _run(cfg, root)


class _AgentLines(Observer):
    """What one agent is doing, for `-v`: tool calls, retries, pacing, the
    wrap-up. Nothing otherwise — the item's own line says how it ended."""

    def __init__(self, kind: AgentKind, title: str, item_id: str, *, verbose: bool) -> None:
        # A judging pass has no item id; it is named by its label instead
        self._prefix = f"{item_id or '-'} · " if kind == "item" else f"__judge__ · {title} "
        self._verbose = verbose

    def progress(self, kind: str, detail: str) -> None:
        if self._verbose:
            _line(f"    {self._prefix}{kind}: {detail}")


def _line(text: str) -> None:
    print(text, flush=True)


def _config_source(cfg: Config) -> None:
    """Two files, named separately.

    Which file a setting came from is the question this section exists to
    answer, and "the config" stopped being a single answer.
    """
    print("Config:")
    if cfg.source is None:
        print(f"  · {home_config_path()}   [no file — settings on defaults]")
    else:
        origin = "default location" if cfg.source == str(home_config_path()) else "--config"
        print(f"  ✓ {cfg.source}   [{origin}, settings]")

    own = provider_config_path()
    if cfg.provider_source is None:
        print(f"  · {own}   [no file — provider on defaults]")
    elif cfg.provider_source == str(own):
        print(f"  ✓ {cfg.provider_source}   [default location, provider]")
    else:
        print(f"  ✓ {cfg.provider_source}   [provider, from the combined file]")

    if cfg.provider_notice:
        print(f"  ⚠ {cfg.provider_notice}")


def _provider(cfg: Config) -> None:
    print("Provider:")
    print(f"  base_url     {cfg.provider.base_url}")
    print(f"  key          {cfg.provider.api_key_origin()}")
    print(f"  pacing       {_pacing(cfg.provider.rate_limits)}")


def _pacing(limits: RateLimits) -> str:
    """Per-minute ceilings a run will hold itself to. Worth printing: a run that
    paces itself is slower on purpose, and that should not have to be guessed."""
    configured = {
        "prompt": limits.prompt_tokens_per_minute,
        "uncached": limits.uncached_prompt_tokens_per_minute,
        "generated": limits.generated_tokens_per_minute,
        "requests": limits.requests_per_minute,
    }
    named = ", ".join(f"{name} {value}/min" for name, value in configured.items() if value)
    adopting = "adopting what the provider advertises" if limits.adopt_advertised else ""
    # A 429 holds every agent back whether or not anything here is set
    return " · ".join(filter(None, [named or "no ceilings set", adopting]))


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
    print(f"  checklist_dir  {overrides.checklist_dir(cfg, root)}")
    _prompt_sources(cfg, root)
    print(f"  templates      {overrides.templates_dir(cfg, root) or 'bundled'}")
    print(f"  reports        {', '.join(cfg.run.report_formats)}")
    language = language_name(cfg.run.output_language)
    print(f"  output lang    {language or 'not set (model answers in the prompt language)'}")
    print(f"  output_dir     {cfg.run.output_dir}")
    print(f"  concurrency    {cfg.run.concurrency}")
    print(f"  reference pass {'on' if cfg.run.resolve_references else 'off'}")
    scope_state = f"±{cfg.run.scope_margin} lines" if cfg.run.enforce_scope else "off"
    print(f"  scope gate     {scope_state}")
    gated = "" if cfg.run.fail_on == exit_codes.NEVER else " (exits 1 at this severity or worse)"
    print(f"  fail_on        {cfg.run.fail_on}{gated}")
    judge_state = "off" if not cfg.run.enable_judge else cfg.run.judge_mode.replace("_", "-")
    print(f"  judge          {judge_state}")


def _prompt_sources(cfg: Config, root: Path) -> None:
    """Only overridden templates are named — listing four bundled paths every
    time buries the one line that says the run is off the default texts."""
    try:
        prompts = Prompts.for_run(cfg, root)
    except PromptError as exc:
        print(f"  prompts        error: {exc}")
        return

    custom = prompts.overridden
    if not custom:
        print("  prompts        bundled")
        return
    print(f"  prompts        {overrides.prompts_dir(cfg, root)}, "
          f"custom: {len(custom)} of {len(prompts.sources)}")
    for name, source in custom.items():
        print(f"    {name:<14} {source}")


def _thinking(value: bool | None) -> str:
    return {None: "model default", True: "on", False: "off"}[value]
