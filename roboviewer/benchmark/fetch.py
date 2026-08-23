"""Turning one entry of the index into a clone the reviewer can be pointed at.

The order matters more than any of the steps: nothing is written where a later
run would find it until both halves — the clone and the comments — are there.
A failure anywhere leaves the entry either as it was or absent, never partly
rebuilt, so the benchmark can be trusted after an interrupted run.

Rate limiting is the one failure that is not about the entry: every entry after
it would fail the same way, so it is raised rather than reported and the caller
stops.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import store as store_module
from .clone import CloneError, prepare
from .github import GitHub, GitHubError, RateLimited, Thread
from .items import Entry
from .store import Store

Status = Literal["cached", "built", "failed"]


@dataclass(frozen=True)
class Result:
    """What became of one entry, in the terms the run is reported in."""

    entry: Entry
    status: Status
    detail: str
    path: Path | None = None
    # "known", "unknown", or "" when the entry was not built
    resolution: str = ""
    # Set when the review was written against some other commit than the head
    # this entry claims — see `reviewed_head`.
    reviewed_head: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


def fetch(entry: Entry, store: Store, github: GitHub, *, refresh: bool = False) -> Result:
    """Fetch what is missing and publish the entry, or say why it could not be.

    Raises `RateLimited`, which is the caller's signal to stop rather than to
    keep asking for the same refusal.
    """
    if not refresh and store.is_built(entry):
        return Result(
            entry=entry,
            status="cached",
            detail="already built",
            path=store.repo_dir(entry),
            resolution=store.resolution_of(entry),
        )

    resolution = store_module.KNOWN if github.resolution_known else store_module.UNKNOWN
    building = store.open_build(entry)
    try:
        prepare(
            building,
            entry.pull.clone_url,
            entry.base,
            entry.head,
            # The head reviewers saw is frequently unreachable from any branch;
            # GitHub keeps it under the pull request's own ref.
            fallback_refs=[f"refs/pull/{entry.pull.number}/head"],
        )
        threads = github.review_threads(entry.pull)
        path = store.publish(entry, threads, resolution=resolution)
    except RateLimited:
        store.discard(entry)
        raise
    except (CloneError, GitHubError, OSError) as exc:
        store.discard(entry)
        return Result(entry=entry, status="failed", detail=str(exc))

    return Result(
        entry=entry,
        status="built",
        detail=_built_detail(threads),
        path=path,
        resolution=resolution,
        reviewed_head=reviewed_head(threads, entry.head),
    )


def reviewed_head(threads: list[Thread], head: str) -> str:
    """The commit most of the review was written against, when `head` is not it.

    An entry positioned after the review measures nothing: the author has
    already changed what reviewers pointed at, so every hit is impossible. The
    trap is that the pull request API hands out the branch tip, which is exactly
    the wrong commit whenever the author pushed fixes — and nothing else here
    can tell the two apart. Empty when the head is among the commits reviewers
    commented on, which includes the ordinary case of a review spanning rounds.
    """
    commented = [thread.commit for thread in threads if thread.commit]
    if not commented or head in commented:
        return ""
    return Counter(commented).most_common(1)[0][0]


def _built_detail(threads: list[Thread]) -> str:
    comments = sum(len(thread.comments) for thread in threads)
    return f"{comments} review comment(s) in {len(threads)} thread(s)"
