"""Reviewing every entry of the index with the tool itself.

One `benchmark run` is one directory under `<root>/runs/`, stamped with the
time it started: the tool's own output for each entry underneath it, and a
summary beside them that says what each review came to. The summary is
rewritten after every review, so at any moment it covers everything finished
so far — a run killed halfway still leaves its tables behind. The reviews go through
`roboviewer.cli.main` with the flags passed through unchanged, the way
`measure.trace review` does it — so there is no second command line to keep in
step, and a flag that works on the tool works here.

An entry that is not on disk yet is fetched first; one that cannot be is
reported and skipped, and the others still run. The tool's exit code per entry
is kept as it was: 0 and 1 are a review that finished, anything else is one
that did not. A tool that raises instead of exiting is that one entry's
failure too — recorded with -1 for the exit code it never gave, and the run
moves on.

A run can review every entry several times — `repeats` — and then the summary
carries statistics over the repeats, computed in `stats`: tokens, time, and how
much the repeats agree with each other.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..models import SEVERITY_LABEL, ReviewRun, Usage
from ..observer import Observer, RunObserver
from . import fetch as fetching
from . import stats
from .github import GitHub
from .items import Entry
from .store import Store

SUMMARY = "summary.json"
SUMMARY_PAGE = "summary.md"
# Flags the benchmark sets itself: which repository is reviewed, and between
# which two commits. A flag here would point the review at something other than
# the entry.
OWN_FLAGS = ("--repo", "--from", "--into")

Status = Literal["reviewed", "stopped", "not_fetched"]
# What runs one review: the tool's `main`, injectable so the suite can run a
# benchmark without a provider.
Review = Callable[[list[str], RunObserver], int]


@dataclass
class Outcome:
    """What one entry came to, in the terms the summary is written in."""

    entry: Entry
    status: Status
    code: int
    seconds: float
    detail: str = ""
    run: ReviewRun | None = None
    directory: Path | None = None
    attempt: int = 1

    @property
    def findings(self) -> int:
        return len(self.run.findings) if self.run else 0

    @property
    def confirmed(self) -> int:
        return len(self.run.confirmed()) if self.run else 0

    @property
    def out_of_scope(self) -> int:
        return len(self.run.out_of_scope) if self.run else 0

    @property
    def with_line(self) -> int:
        """Findings that name a line — the ones a forge can take as comments."""
        return sum(1 for finding in self.run.findings if finding.line) if self.run else 0

    @property
    def ok(self) -> bool:
        return self.status == "reviewed"

    @property
    def crashed(self) -> bool:
        """The tool raised instead of exiting — the same review would raise again."""
        return self.status == "stopped" and self.code < 0


@dataclass
class Benchmark:
    """One run over the index: where it writes, and what it has done so far."""

    directory: Path
    flags: list[str]
    repeats: int = 1
    outcomes: list[Outcome] = field(default_factory=list)
    # Guards the outcomes and the summary files when entries run in parallel
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(outcome.ok for outcome in self.outcomes)


def start(
    store: Store, flags: list[str], *, repeats: int = 1, stamp: str | None = None
) -> Benchmark:
    """A directory for this run. `stamp` is injectable for the suite; a real
    run is named for the minute it began."""
    for flag in flags:
        if flag in OWN_FLAGS or any(flag.startswith(own + "=") for own in OWN_FLAGS):
            raise ValueError(
                f"{flag} is not a benchmark flag: each entry is reviewed in its own clone, "
                "between the two commits the index names"
            )
    if repeats < 1:
        raise ValueError(f"--repeats {repeats}: a run reviews every entry at least once")
    stamp = stamp or datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    directory = _fresh(store.runs, stamp)
    return Benchmark(directory=directory, flags=list(flags), repeats=repeats)


def review_entry(
    benchmark: Benchmark,
    entry: Entry,
    store: Store,
    github: GitHub,
    *,
    refresh: bool = False,
    review: Review,
) -> Outcome:
    """Fetch the entry if it is not there, review it, and record the outcome.
    Reviewing the same entry again is the next attempt of it. Every recorded
    outcome rewrites the summary, so an interrupted run keeps what it finished.
    A tool that raises instead of exiting is recorded the same way a bad exit
    code is, so one entry's crash does not take the rest of the run with it.

    Raises `RateLimited` from the fetch, which is the caller's signal to stop
    asking rather than to fail every remaining entry the same way.
    """
    started = time.monotonic()
    with benchmark.lock:
        attempt = 1 + sum(1 for outcome in benchmark.outcomes if outcome.entry.id == entry.id)
    if refresh or not store.is_built(entry):
        fetched = fetching.fetch(entry, store, github, refresh=refresh)
        if not fetched.ok:
            outcome = Outcome(
                entry=entry,
                status="not_fetched",
                code=-1,
                seconds=time.monotonic() - started,
                detail=fetched.detail,
                attempt=attempt,
            )
            _record(benchmark, outcome)
            return outcome

    argv = [
        "review",
        "--into", entry.base,
        "--from", entry.head,
        "--repo", str(store.repo_dir(entry)),
    ]
    if not _names_output(benchmark.flags):
        # Absolute, or the tool resolves it against the clone --repo points it at
        argv += ["-o", str(benchmark.directory.absolute())]
    argv += benchmark.flags

    watcher = _Collector()
    try:
        code = review(argv, watcher)
        detail = "" if code in (0, 1) else f"roboviewer exited with {code}"
    except Exception as exc:  # noqa: BLE001 — one entry's crash, not the run's
        code = -1
        detail = f"roboviewer raised {type(exc).__name__}: {exc}"
    outcome = Outcome(
        entry=entry,
        status="reviewed" if code in (0, 1) else "stopped",
        code=code,
        seconds=time.monotonic() - started,
        detail=detail,
        run=watcher.run,
        directory=watcher.directory,
        attempt=attempt,
    )
    _record(benchmark, outcome)
    return outcome


def write_summary(benchmark: Benchmark) -> Path:
    """The machine-readable summary and the page beside it."""
    groups, run_group = _statistics(benchmark)
    payload = {
        "format": 1,
        "flags": benchmark.flags,
        "repeats": benchmark.repeats,
        "entries": [_row(outcome) for outcome in benchmark.outcomes],
        "stats": {
            "entries": [stats.payload(group) for group in groups],
            "run": stats.payload(run_group),
        },
    }
    (benchmark.directory / SUMMARY).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (benchmark.directory / SUMMARY_PAGE).write_text(_page(benchmark), encoding="utf-8")
    return benchmark.directory / SUMMARY


def _record(benchmark: Benchmark, outcome: Outcome) -> None:
    """One outcome in, summary rewritten — atomically against parallel entries."""
    with benchmark.lock:
        benchmark.outcomes.append(outcome)
        write_summary(benchmark)


def _fresh(runs: Path, stamp: str) -> Path:
    """`runs/<stamp>`, or `<stamp>-2` and so on when two runs start within one
    second — a run must never write into another run's directory."""
    for attempt in range(1, 100):
        directory = runs / (stamp if attempt == 1 else f"{stamp}-{attempt}")
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        return directory
    raise FileExistsError(f"{runs / stamp}: no free run directory after 99 tries")


class _Collector(Observer):
    """Keeps the run the tool reported, so the summary is read from the model
    rather than scraped from the console."""

    def __init__(self) -> None:
        self.run: ReviewRun | None = None
        self.directory: Path | None = None

    def run_started(self, run: ReviewRun, directory: Path) -> None:
        self.run = run
        self.directory = directory

    def run_finished(self, run: ReviewRun, message: str) -> None:  # noqa: ARG002
        self.run = run


def _names_output(flags: list[str]) -> bool:
    return any(flag in ("-o", "--output") or flag.startswith("--output=") for flag in flags)


def _row(outcome: Outcome) -> dict[str, object]:
    run = outcome.run
    usage = run.total_usage if run else None
    review = stats.Review(run=run, seconds=outcome.seconds) if run else None
    return {
        "id": outcome.entry.id,
        "url": outcome.entry.url,
        "attempt": outcome.attempt,
        "status": outcome.status,
        "exit_code": outcome.code,
        "detail": outcome.detail,
        "seconds": round(outcome.seconds, 1),
        "run_id": run.run_id if run else "",
        "model": run.model if run else "",
        "directory": str(outcome.directory) if outcome.directory else "",
        "findings": outcome.findings,
        "confirmed": outcome.confirmed,
        "out_of_scope": outcome.out_of_scope,
        "with_line": outcome.with_line,
        "items": len(run.items) if run else 0,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "runner": _usage_row(review.runner_usage if review else None),
        "judge": _usage_row(review.judge_usage if review else None),
    }


def _page(benchmark: Benchmark) -> str:
    lines = [
        f"# Benchmark run {benchmark.directory.name}",
        "",
        f"Flags: `{' '.join(benchmark.flags) or '(none)'}`",
    ]
    if benchmark.repeats > 1:
        lines.append(f"Repeats: {benchmark.repeats} per entry")
    lines += [
        "",
        "| Entry | Status | Findings | Confirmed | Out of scope | With line | Tokens | Time "
        "| Run |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for outcome in benchmark.outcomes:
        usage = outcome.run.total_usage if outcome.run else None
        tokens = usage.prompt_tokens + usage.completion_tokens if usage else 0
        status = outcome.status + (f" ({outcome.detail})" if outcome.detail else "")
        entry = outcome.entry.id + (f" #{outcome.attempt}" if benchmark.repeats > 1 else "")
        lines.append(
            f"| {entry} | {status} | {outcome.findings} | {outcome.confirmed} | "
            f"{outcome.out_of_scope} | {outcome.with_line} | {tokens} | {outcome.seconds:.0f}s | "
            f"{outcome.directory or '—'} |"
        )
    reviewed = [outcome for outcome in benchmark.outcomes if outcome.ok]
    lines += [
        "",
        f"{len(reviewed)} of {len(benchmark.outcomes)} reviewed, "
        f"{sum(o.findings for o in reviewed)} findings, "
        f"{sum(o.confirmed for o in reviewed)} confirmed, "
        f"{sum(o.with_line for o in reviewed)} with a line.",
    ]
    lines += _statistics_pages(benchmark)
    lines.append("")
    return "\n".join(lines)


def _statistics(benchmark: Benchmark) -> tuple[list[stats.Group], stats.Group]:
    """One group per entry over its finished reviews, and one over them all."""
    ordered: dict[str, list[stats.Review]] = {}
    for outcome in benchmark.outcomes:
        if outcome.ok and outcome.run is not None:
            ordered.setdefault(outcome.entry.id, []).append(
                stats.Review(run=outcome.run, seconds=outcome.seconds)
            )
    groups = [stats.group(entry_id, reviews) for entry_id, reviews in ordered.items()]
    pooled = [review for reviews in ordered.values() for review in reviews]
    return groups, stats.whole("Whole run", groups, pooled)


def _statistics_pages(benchmark: Benchmark) -> list[str]:
    groups, run_group = _statistics(benchmark)
    lines = ["", "## Statistics", ""]
    if benchmark.repeats < 2:
        lines.append(
            "Self-consistency needs at least two repeats of an entry — `--repeats N` adds them."
        )
        lines.append("")
    for group in groups:
        lines += _stats_table(group)
    lines += _stats_table(run_group)
    return lines[:-1]  # the page's closing blank line is _page's own


def _stats_table(group: stats.Group) -> list[str]:
    per_severity = [
        _ratio_row(f"Self-consistency, {SEVERITY_LABEL[severity].lower()}", value)
        for severity, value in group.consistency_by_severity.items()
    ]
    return [
        f"### {group.title} — {group.reviews} review(s)",
        "",
        "| Metric | Total | Mean | p50 | p90 | Max |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        _total_row("Runner prompt tokens", group.runner.prompt_tokens),
        _total_row("Runner completion tokens", group.runner.completion_tokens),
        _total_row("Runner cached tokens", group.runner.cached_tokens),
        _spread_row("Runner tokens per review", group.runner_tokens, _tokens),
        _total_row("Judge prompt tokens", group.judge.prompt_tokens),
        _total_row("Judge completion tokens", group.judge.completion_tokens),
        _total_row("Judge cached tokens", group.judge.cached_tokens),
        _spread_row("Judge tokens per review", group.judge_tokens, _tokens),
        _spread_row("Review time (s)", group.seconds, _seconds),
        _ratio_row("Self-consistency", group.consistency),
        *per_severity,
        "",
    ]


def _total_row(metric: str, value: int) -> str:
    return f"| {metric} | {value} | | | | |"


def _spread_row(
    metric: str, spread: stats.Distribution | None, note: Callable[[float], str]
) -> str:
    if spread is None:
        return f"| {metric} | | — | — | — | — |"
    return (
        f"| {metric} | | {note(spread.mean)} | {note(spread.p50)} | "
        f"{note(spread.p90)} | {note(spread.max)} |"
    )


def _ratio_row(metric: str, value: float | None) -> str:
    return f"| {metric} | {'—' if value is None else f'{value:.2f}'} | | | | |"


def _usage_row(usage: Usage | None) -> dict[str, int]:
    return {
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "cached_tokens": usage.cached_tokens if usage else 0,
    }


def _tokens(value: float) -> str:
    return str(round(value))


def _seconds(value: float) -> str:
    return f"{value:.1f}"
