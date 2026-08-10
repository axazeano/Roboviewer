"""Where a built entry lives, and how it is written so it is never half-built.

    <corpus>/<id>/repo/            the clone, at the head reviewers saw
    <corpus>/<id>/comments.json    what reviewers said
    <corpus>/<id>/corpus.json      what this was built from

Work happens under `<corpus>/.building/<id>` and the directory is renamed into
place once the marker is written, so `<corpus>/<id>` either holds a complete
entry or does not exist. An entry that is already there is moved into the
building directory rather than re-cloned: a rebuild of a changed head should
cost one fetch, not another copy of the repository's history.

The marker is the whole cache. A rerun that finds it matching, with both commits
in the clone and the comments saved, does nothing at all — which is what keeps a
rerun off the network.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import clone
from .entries import Entry
from .github import Thread

FORMAT = 1
MARKER = "corpus.json"
COMMENTS = "comments.json"
REPO = "repo"
BUILDING = ".building"

# Whether thread resolution could be read at all — a token buys it, anonymous
# requests do not have it to give.
KNOWN = "known"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Store:
    """The corpus root, and every path derived from it."""

    root: Path

    def entry_dir(self, entry: Entry) -> Path:
        return self.root / entry.id

    def repo_dir(self, entry: Entry) -> Path:
        return self.entry_dir(entry) / REPO

    def is_built(self, entry: Entry) -> bool:
        return _is_built(self.entry_dir(entry), entry)

    def resolution_of(self, entry: Entry) -> str:
        """What the built entry knows about thread resolution, or "" when there
        is nothing built. Read to tell someone their new token would buy them
        something the stored copy does not have."""
        marker = _marker(self.entry_dir(entry))
        return str(marker.get("resolution", "")) if marker else ""

    def open_build(self, entry: Entry) -> Path:
        """A directory to build into, carrying over whatever was already there."""
        building = self._building_dir(entry)
        if building.exists():
            shutil.rmtree(building)
        building.parent.mkdir(parents=True, exist_ok=True)
        existing = self.entry_dir(entry)
        if existing.exists():
            # Cheap on any filesystem, and it saves refetching the history. The
            # marker moves with it, which is what lets `discard` put an intact
            # entry back after a failed refresh.
            existing.rename(building)
        else:
            building.mkdir()
        return building

    def publish(self, entry: Entry, threads: list[Thread], *, resolution: str) -> Path:
        """Save the comments, stamp the marker, and move the entry into place."""
        building = self._building_dir(entry)
        _write_comments(building / COMMENTS, entry, threads, resolution=resolution)
        (building / MARKER).write_text(
            json.dumps(
                {
                    "format": FORMAT,
                    "id": entry.id,
                    "url": entry.url,
                    "base": entry.base,
                    "head": entry.head,
                    "threads": len(threads),
                    "resolution": resolution,
                    "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        target = self.entry_dir(entry)
        if target.exists():
            shutil.rmtree(target)
        building.rename(target)
        return target

    def discard(self, entry: Entry) -> None:
        """Give up on a build: put back what was already complete, delete the rest.

        A failure that happens to be a rate limit must not cost someone the
        clone they already had — but nothing incomplete may be left where a
        later run would read it as built.
        """
        building = self._building_dir(entry)
        if not building.exists():
            return
        if _is_built(building, entry) and not self.entry_dir(entry).exists():
            building.rename(self.entry_dir(entry))
            return
        shutil.rmtree(building, ignore_errors=True)

    def _building_dir(self, entry: Entry) -> Path:
        return self.root / BUILDING / entry.id


def default_root() -> Path:
    """Outside any repository under measurement, and outside this one.

    A corpus inside a reviewed repository would be diffed, excluded, indexed and
    committed by accident, so the default is the user's cache directory and the
    only ways to move it are explicit: `--corpus`, or $ROBOVIEWER_CORPUS.
    """
    named = os.environ.get("ROBOVIEWER_CORPUS", "").strip()
    if named:
        return Path(named).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    root = Path(cache).expanduser() if cache else Path.home() / ".cache"
    return root / "roboviewer" / "corpus"


def _is_built(directory: Path, entry: Entry) -> bool:
    """Complete means all three: the marker says it was built from this entry,
    both commits are in the clone, and the comments are on disk."""
    marker = _marker(directory)
    if not marker:
        return False
    built_from = (marker.get("format"), marker.get("url"), marker.get("base"), marker.get("head"))
    if built_from != (FORMAT, entry.url, entry.base, entry.head):
        return False
    if not (directory / COMMENTS).is_file():
        return False
    return clone.has_commits(directory / REPO, entry.base, entry.head)


def _marker(directory: Path) -> dict[str, object]:
    path = directory / MARKER
    if not path.is_file():
        return {}
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return marker if isinstance(marker, dict) else {}


def _write_comments(
    path: Path, entry: Entry, threads: list[Thread], *, resolution: str
) -> None:
    path.write_text(
        json.dumps(
            {
                "pull_request": entry.url,
                "base": entry.base,
                "head": entry.head,
                # "unknown" means nobody asked, not that nothing was resolved:
                # the anonymous API has no such field. See github.py.
                "resolution": resolution,
                "threads": [
                    {
                        "file": thread.file,
                        "line": thread.line,
                        "resolved": thread.resolved,
                        "comments": [
                            {
                                "author": comment.author,
                                "body": comment.body,
                                "created_at": comment.created_at,
                                "url": comment.url,
                            }
                            for comment in thread.comments
                        ],
                    }
                    for thread in threads
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
