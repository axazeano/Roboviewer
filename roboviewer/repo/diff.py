"""What a branch changed, read out of `git diff`.

Two readings of the same diff, for two purposes. `changed_files` is the list —
which files, how many lines each — and `diff_text` the hunks for the files that
go to the agent as fragments. `change_map` parses `git diff -U0` into the exact
lines each file gained and lost, which is what the file markup is rendered from
and what the scope gate measures a finding against.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models import DiffStat
from .git import git

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileChanges:
    """Which lines of the new file version the MR touched."""

    added: set[int] = field(default_factory=set)
    # Key is the new-version line number the removed lines sat BEFORE.
    removed_before: dict[int, list[str]] = field(default_factory=dict)


def changed_files(root: Path, base: str, source: str, excludes: list[str]) -> list[DiffStat]:
    name_status = git(root, "diff", "--name-status", "-M", f"{base}..{source}")
    statuses: dict[str, str] = {}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        # Renames come as: R096<TAB>old<TAB>new
        path = parts[-1]
        statuses[path] = parts[0]

    numstat = git(root, "diff", "--numstat", "-M", f"{base}..{source}")
    result: list[DiffStat] = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, removed_raw, path = parts[0], parts[1], parts[-1]
        if _excluded(path, excludes):
            continue
        result.append(
            DiffStat(
                file=path,
                status=statuses.get(path, "M"),
                added=int(added_raw) if added_raw.isdigit() else 0,
                removed=int(removed_raw) if removed_raw.isdigit() else 0,
            )
        )
    return sorted(result, key=lambda s: s.file)


def diff_text(
    root: Path,
    base: str,
    source: str,
    files: list[str],
    context_lines: int = 5,
    max_chars: int = 300_000,
) -> tuple[str, bool]:
    """Returns (diff text, whether it was truncated)."""
    if not files:
        return "", False
    text = git(
        root, "diff", "-M", f"--unified={context_lines}", "--no-color",
        f"{base}..{source}", "--", *files,
    )
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... diff truncated; read the rest with the tools ...]", True
    return text, False


def change_map(root: Path, base: str, source: str, files: list[str]) -> dict[str, FileChanges]:
    """Parses `git diff -U0` into a per-file map of changed lines.

    -U0 yields hunks with no context lines, so parsing is unambiguous: everything
    in a hunk body is either an addition or a removal.
    """
    if not files:
        return {}

    out = git(root, "diff", "-M", "--unified=0", "--no-color", f"{base}..{source}", "--", *files)
    scan = _ChangeScan()
    for raw in out.splitlines():
        scan.feed(raw)
    return scan.changes


def _excluded(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(f"/{path}", pat) for pat in patterns)


def _anchor(hunk: re.Match[str]) -> int:
    """Where in the new file this hunk starts counting.

    `@@ -2 +2 @@` puts the hunk at line 2, and a removal in it sat before what
    is now line 2 — the line that replaced it. A hunk that adds nothing is
    written `@@ -2 +1,0 @@`, where 1 is the last line that survived rather than
    the position the removal held: the deleted line sat before line 2. Without
    the +1 the markup prints it a line too high and the scope gate anchors it a
    line off.
    """
    start = int(hunk.group(1))
    count = int(hunk.group(2)) if hunk.group(2) is not None else 1
    return start if count else start + 1


class _ChangeScan:
    """`git diff -U0`, read one line at a time.

    Three states, and telling them apart is the whole job: between files, inside
    a file header, inside a hunk body. Outside a header, a line beginning with
    `---` is a deleted line of code rather than the header it looks like, and
    reading it as a header would drop the rest of the file.
    """

    def __init__(self) -> None:
        self.changes: dict[str, FileChanges] = {}
        self._file: FileChanges | None = None
        self._new_line = 0
        self._in_header = False

    def feed(self, raw: str) -> None:
        if raw.startswith("diff --git "):
            self._file, self._in_header = None, True
            return
        if self._in_header and not self._read_header(raw):
            return

        hunk = _HUNK_RE.match(raw)
        if hunk:
            self._new_line = _anchor(hunk)
            return
        if self._file is not None and raw:
            self._read_body(self._file, raw)

    def _read_header(self, raw: str) -> bool:
        """One line of a file header — index / mode / similarity / --- / +++.

        Returns True once the header is over, meaning this line is a hunk header
        and the caller has to go on and read it.
        """
        if raw.startswith("+++ "):
            self._file = self._open(raw[4:].strip())
            return False
        if not raw.startswith("@@"):
            return False
        self._in_header = False
        return True

    def _open(self, path: str) -> FileChanges | None:
        """The entry the following hunks belong to. A deleted file has no new
        version to number lines in, so it gets none."""
        if path == "/dev/null":
            return None
        key = path[2:] if path.startswith(("a/", "b/")) else path
        return self.changes.setdefault(key, FileChanges())

    def _read_body(self, file: FileChanges, raw: str) -> None:
        if raw[0] == "+":
            file.added.add(self._new_line)
            self._new_line += 1
        elif raw[0] == "-":
            file.removed_before.setdefault(self._new_line, []).append(raw[1:])
        # "\ No newline at end of file" and friends are ignored
