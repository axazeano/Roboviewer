"""Reviewing every entry of the index with the tool itself.

One `benchmark run` is one directory under `<root>/runs/`, stamped with the
time it started: the tool's own output for each entry underneath it, and a
summary beside them that says what each review came to. The reviews go through
`roboviewer.cli.main` with the flags passed through unchanged, the way
`measure.trace review` does it — so there is no second command line to keep in
step, and a flag that works on the tool works here.

An entry that is not on disk yet is fetched first; one that cannot be is
reported and skipped, and the others still run. The tool's exit code per entry
is kept as it was: 0 and 1 are a review that finished, anything else is one
that did not.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ..models import ReviewRun
from ..observer import Observer, RunObserver
from . import fetch as fetching
from .github import GitHub
from .items import Entry
from .store import Store

SUMMARY = "summary.json"
SUMMARY_PAGE = "summary.md"
# Flags that would point the review somewhere other than the entry's clone.
OWN_FLAGS = ("-C", "--repo")

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
    def ok(self) -> bool:
        return self.status == "reviewed"


@dataclass
class Benchmark:
    """One run over the index: where it writes, and what it has done so far."""

    directory: Path
    flags: list[str]
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(outcome.ok for outcome in self.outcomes)


def start(store: Store, flags: list[str], *, stamp: str | None = None) -> Benchmark:
    """A directory for this run. `stamp` is injectable for the suite; a real
    run is named for the minute it began."""
    for flag in flags:
        if flag in OWN_FLAGS or flag.startswith("--repo="):
            raise ValueError(
                f"{flag} is not a benchmark flag: each entry is reviewed in its own clone"
            )
    stamp = stamp or datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
    directory = store.runs / stamp
    directory.mkdir(parents=True, exist_ok=False)
    return Benchmark(directory=directory, flags=list(flags))


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

    Raises `RateLimited` from the fetch, which is the caller's signal to stop
    asking rather than to fail every remaining entry the same way.
    """
    started = time.monotonic()
    if refresh or not store.is_built(entry):
        fetched = fetching.fetch(entry, store, github, refresh=refresh)
        if not fetched.ok:
            outcome = Outcome(
                entry=entry,
                status="not_fetched",
                code=-1,
                seconds=time.monotonic() - started,
                detail=fetched.detail,
            )
            benchmark.outcomes.append(outcome)
            return outcome

    argv = [entry.base, entry.head, "-C", str(store.repo_dir(entry))]
    if not _names_output(benchmark.flags):
        argv += ["-o", str(benchmark.directory)]
    argv += benchmark.flags

    watcher = _Collector()
    code = review(argv, watcher)
    outcome = Outcome(
        entry=entry,
        status="reviewed" if code in (0, 1) else "stopped",
        code=code,
        seconds=time.monotonic() - started,
        detail="" if code in (0, 1) else f"roboviewer exited with {code}",
        run=watcher.run,
        directory=watcher.directory,
    )
    benchmark.outcomes.append(outcome)
    return outcome


def write_summary(benchmark: Benchmark) -> Path:
    """The machine-readable summary and the page beside it."""
    payload = {
        "format": 1,
        "flags": benchmark.flags,
        "entries": [_row(outcome) for outcome in benchmark.outcomes],
    }
    (benchmark.directory / SUMMARY).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (benchmark.directory / SUMMARY_PAGE).write_text(_page(benchmark), encoding="utf-8")
    return benchmark.directory / SUMMARY


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
    return {
        "id": outcome.entry.id,
        "url": outcome.entry.url,
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
        "items": len(run.items) if run else 0,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
    }


def _page(benchmark: Benchmark) -> str:
    lines = [
        f"# Benchmark run {benchmark.directory.name}",
        "",
        f"Flags: `{' '.join(benchmark.flags) or '(none)'}`",
        "",
        "| Entry | Status | Findings | Confirmed | Out of scope | Tokens | Time | Run |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for outcome in benchmark.outcomes:
        usage = outcome.run.total_usage if outcome.run else None
        tokens = usage.prompt_tokens + usage.completion_tokens if usage else 0
        status = outcome.status + (f" ({outcome.detail})" if outcome.detail else "")
        lines.append(
            f"| {outcome.entry.id} | {status} | {outcome.findings} | {outcome.confirmed} | "
            f"{outcome.out_of_scope} | {tokens} | {outcome.seconds:.0f}s | "
            f"{outcome.directory or '—'} |"
        )
    reviewed = [outcome for outcome in benchmark.outcomes if outcome.ok]
    lines += [
        "",
        f"{len(reviewed)} of {len(benchmark.outcomes)} reviewed, "
        f"{sum(o.findings for o in reviewed)} findings, "
        f"{sum(o.confirmed for o in reviewed)} confirmed.",
        "",
    ]
    return "\n".join(lines)
