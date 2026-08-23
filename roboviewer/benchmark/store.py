"""The benchmarks directory: where the index, the clones, the references and
the runs live, and how an entry is fetched so it is never half-built.

    <root>/items.toml               the index: one [[entry]] per merge request
    <root>/references/<id>.toml     what a good review of that entry finds
    <root>/repos/<id>/              the clone, at the head reviewers saw
    <root>/comments/<id>.json       what reviewers said
    <root>/runs/<stamp>/<id>/       what `benchmark run` produced

The root is `benchmarks/` in the current directory unless `--root` or
$ROBOVIEWER_BENCHMARKS says otherwise; the index and the references are meant
to be committed, the rest is not.

A clone is fetched under `<root>/repos/.building/<id>` and renamed into place
once its marker is written, so `<root>/repos/<id>` either holds a complete
clone or does not exist. A clone that is already there is moved into the
building directory rather than re-cloned: a rebuild of a changed head should
cost one fetch, not another copy of the repository's history.

The marker lives inside the clone's `.git/`, where git ignores it and where it
goes wherever the clone goes: delete the clone by hand and the entry is simply
not built. It is the whole cache — a rerun that finds it matching, with both
commits in the clone and the comments saved, does nothing at all, which is what
keeps a rerun off the network.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import clone
from .github import Thread
from .items import Entry

FORMAT = 1
ITEMS = "items.toml"
REFERENCES = "references"
REPOS = "repos"
COMMENTS = "comments"
RUNS = "runs"
BUILDING = ".building"
MARKER = Path(".git") / "benchmark.json"

# Whether thread resolution could be read at all — a token buys it, anonymous
# requests do not have it to give.
KNOWN = "known"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Store:
    """The benchmarks root, and every path derived from it."""

    root: Path

    @property
    def items(self) -> Path:
        return self.root / ITEMS

    @property
    def runs(self) -> Path:
        return self.root / RUNS

    def reference(self, entry: Entry) -> Path:
        return self.root / REFERENCES / f"{entry.id}.toml"

    def repo_dir(self, entry: Entry) -> Path:
        return self.root / REPOS / entry.id

    def comments_path(self, entry: Entry) -> Path:
        return self.root / COMMENTS / f"{entry.id}.json"

    def is_built(self, entry: Entry) -> bool:
        return self._is_built(self.repo_dir(entry), entry)

    def resolution_of(self, entry: Entry) -> str:
        """What the built entry knows about thread resolution, or "" when there
        is nothing built. Read to tell someone their new token would buy them
        something the stored copy does not have."""
        marker = _marker(self.repo_dir(entry))
        return str(marker.get("resolution", "")) if marker else ""

    def open_build(self, entry: Entry) -> Path:
        """A directory to clone into, carrying over whatever was already there."""
        building = self._building_dir(entry)
        if building.exists():
            shutil.rmtree(building)
        building.parent.mkdir(parents=True, exist_ok=True)
        existing = self.repo_dir(entry)
        if existing.exists():
            # Cheap on any filesystem, and it saves refetching the history. The
            # marker moves with it, which is what lets `discard` put an intact
            # clone back after a failed refresh.
            existing.rename(building)
        else:
            building.mkdir()
        return building

    def publish(self, entry: Entry, threads: list[Thread], *, resolution: str) -> Path:
        """Save the comments, stamp the marker, and move the clone into place."""
        building = self._building_dir(entry)
        comments = self.comments_path(entry)
        comments.parent.mkdir(parents=True, exist_ok=True)
        _write_comments(comments, entry, threads, resolution=resolution)
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
        target = self.repo_dir(entry)
        if target.exists():
            shutil.rmtree(target)
        building.rename(target)
        return target

    def discard(self, entry: Entry) -> None:
        """Give up on a fetch: put back what was already complete, delete the rest.

        A failure that happens to be a rate limit must not cost someone the
        clone they already had — but nothing incomplete may be left where a
        later run would read it as built.
        """
        building = self._building_dir(entry)
        if not building.exists():
            return
        if self._is_built(building, entry) and not self.repo_dir(entry).exists():
            building.rename(self.repo_dir(entry))
            return
        shutil.rmtree(building, ignore_errors=True)

    def _building_dir(self, entry: Entry) -> Path:
        return self.root / REPOS / BUILDING / entry.id

    def _is_built(self, repo_dir: Path, entry: Entry) -> bool:
        """Complete means all three: the marker says the clone was built from
        this entry, both commits are in it, and the comments are on disk."""
        marker = _marker(repo_dir)
        if not marker:
            return False
        built_from = (
            marker.get("format"), marker.get("url"), marker.get("base"), marker.get("head")
        )
        if built_from != (FORMAT, entry.url, entry.base, entry.head):
            return False
        if not self.comments_path(entry).is_file():
            return False
        return clone.has_commits(repo_dir, entry.base, entry.head)


def default_root() -> Path:
    """`benchmarks/` where the command is run, unless the environment says
    otherwise. The index inside it is committed with the project; the clones
    and the runs beside it are ignored by git."""
    named = os.environ.get("ROBOVIEWER_BENCHMARKS", "").strip()
    if named:
        return Path(named).expanduser()
    return Path("benchmarks")


def _marker(repo_dir: Path) -> dict[str, object]:
    path = repo_dir / MARKER
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
                        # The commit this thread was written against. Kept so a
                        # later reader can check the head above is that commit
                        # rather than a later one, where the fixes already are.
                        "commit": thread.commit,
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
