"""Collecting the diff of the source branch against the target one.

Two key decisions:

1. We compare from the merge-base, not against the target branch directly.
   `git diff target HEAD` would drag in other people's commits that landed on
   target after the branch point — reviewing those is not our job.

2. The agent gets changed files IN FULL with changed lines marked up, not hunks.
   A diff with a few lines of context is the main source of false positives: a
   guard sitting twenty lines above never makes it into the hunk, and the agent
   reports "no handling here" when in fact there is.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import DiffStat
from .resolve import ReferenceReport
from .resolve import check as resolve_check


class GitError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} → {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(start: Path) -> Path:
    try:
        out = _git(["rev-parse", "--show-toplevel"], start)
    except GitError as exc:
        raise GitError(f"{start} is not inside a git repository") from exc
    return Path(out.strip())


def current_branch(root: Path) -> str:
    name = _git(["rev-parse", "--abbrev-ref", "HEAD"], root).strip()
    return name if name != "HEAD" else _git(["rev-parse", "--short", "HEAD"], root).strip()


def resolve_ref(root: Path, ref: str, *, kind: str = "Branch") -> str:
    """Resolve a branch, trying the local name first and then origin/."""
    for candidate in (ref, f"origin/{ref}"):
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return candidate
    raise GitError(f"{kind} found neither as '{ref}' nor as 'origin/{ref}'")


def merge_base(root: Path, target: str, source: str = "HEAD") -> str:
    try:
        return _git(["merge-base", target, source], root).strip()
    except GitError as exc:
        # git says only "→ 1" here, and in CI the reason is nearly always the
        # same one: the branch point was never fetched.
        raise GitError(
            f"{target} and {source} have no common commit in this clone, "
            f"so there is no branch point to diff from"
        ) from exc


def is_shallow(root: Path) -> bool:
    """A clone cut off at N commits — the default in both GitLab and GitHub CI."""
    return _git(["rev-parse", "--is-shallow-repository"], root).strip() == "true"


def head_sha(root: Path, ref: str = "HEAD") -> str:
    return _git(["rev-parse", ref], root).strip()


def _excluded(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(f"/{path}", pat) for pat in patterns)


def changed_files(root: Path, base: str, source: str, excludes: list[str]) -> list[DiffStat]:
    name_status = _git(["diff", "--name-status", "-M", f"{base}..{source}"], root)
    statuses: dict[str, str] = {}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        # Renames come as: R096<TAB>old<TAB>new
        path = parts[-1]
        statuses[path] = parts[0]

    numstat = _git(["diff", "--numstat", "-M", f"{base}..{source}"], root)
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
    args = [
        "diff",
        "-M",
        f"--unified={context_lines}",
        "--no-color",
        f"{base}..{source}",
        "--",
        *files,
    ]
    text = _git(args, root)
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n[... diff truncated; read the rest with the tools ...]", True
    return text, False


def show_file_at(root: Path, ref: str, path: str) -> str | None:
    """File contents at the given revision (used for before/after comparison)."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


# ------------------------------------------------------------------ file annotation
#
# The legend goes into the prompt and must keep describing exactly what
# `annotate_file` emits — the agent reads line numbers off this markup.

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

MARKER_CONTEXT = "   | "
MARKER_ADDED = " + | "
MARKER_REMOVED = " - | "

ANNOTATION_LEGEND = """\
Format: line number in the new version of the file, marker, code.
   12   | code   — line unchanged in this MR, shown for context
   13 + | code   — line added or changed in this MR
      - | code   — line removed in this MR (absent from the new version, so it has no number)\
"""


@dataclass
class FileChanges:
    """Which lines of the new file version the MR touched."""

    added: set[int] = field(default_factory=set)
    # Key is the new-version line number the removed lines sat BEFORE.
    removed_before: dict[int, list[str]] = field(default_factory=dict)


def change_map(root: Path, base: str, source: str, files: list[str]) -> dict[str, FileChanges]:
    """Parses `git diff -U0` into a per-file map of changed lines.

    -U0 yields hunks with no context lines, so parsing is unambiguous: everything
    in a hunk body is either an addition or a removal.
    """
    if not files:
        return {}

    out = _git(["diff", "-M", "--unified=0", "--no-color", f"{base}..{source}", "--", *files], root)
    scan = _ChangeScan()
    for raw in out.splitlines():
        scan.feed(raw)
    return scan.changes


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


def normalise_path(path: str) -> str:
    return path.strip().lstrip("./")


def in_scope(
    changes: dict[str, FileChanges], file: str, line: int | None, margin: int
) -> bool:
    """Does a finding point at what this MR did?

    Line arithmetic over the same map that marked up the files the agent read,
    so the gate and the prompt cannot disagree about what "changed" means. It
    knows nothing about languages: a changed line is a changed line in Swift, Go
    and YAML alike.

    A removal is an anchor too — deleting a method from a protocol is a change,
    and the only line left to point at is the neighbour that survived. Hence the
    margin, which is also what covers a finding that names the declaration a few
    lines above the edit. It is a heuristic and the report keeps what it drops.
    """
    if not changes:
        return True  # no map, no gate — never silently drop everything

    entry = changes.get(normalise_path(file))
    if entry is None:
        return False  # a file this MR never touched
    if line is None:
        return True  # about the file as a whole, which the MR did touch

    anchors = entry.added | set(entry.removed_before)
    return any(abs(line - anchor) <= margin for anchor in anchors)


def looks_binary(text: str) -> bool:
    return "\x00" in text[:4000]


def annotate_file(path: str, stat: DiffStat, content: str, changes: FileChanges) -> str:
    lines = content.splitlines()
    header = (
        f"===== {path} [{stat.status}, +{stat.added}/-{stat.removed}, {len(lines)} lines] ====="
    )
    out = [header]

    for number, code in enumerate(lines, start=1):
        for removed in changes.removed_before.get(number, ()):
            out.append(f"     {MARKER_REMOVED}{removed}")
        marker = MARKER_ADDED if number in changes.added else MARKER_CONTEXT
        out.append(f"{number:>5}{marker}{code}")

    # Removals at the very end of the file are anchored at position len+1
    for removed in changes.removed_before.get(len(lines) + 1, ()):
        out.append(f"     {MARKER_REMOVED}{removed}")

    return "\n".join(out)


def build_annotated(
    root: Path,
    source: str,
    files: list[DiffStat],
    *,
    changes: dict[str, FileChanges],
    max_lines: int,
    max_total_chars: int,
) -> tuple[str, list[str], list[str]]:
    """Renders changed files in full, with markup.

    Returns (text, files inlined in full, files that did not fit).
    The budget is handed out starting from the most heavily changed files: if it
    cannot cover everything, the ones with more edits should be the ones inlined.
    """
    rendered: dict[str, str] = {}
    fallback: list[str] = []
    budget = max_total_chars

    by_weight = sorted(files, key=lambda f: f.added + f.removed, reverse=True)
    for stat in by_weight:
        if stat.status.startswith("D"):
            fallback.append(stat.file)  # a deleted file has no new version
            continue
        content = show_file_at(root, source, stat.file)
        if content is None or looks_binary(content):
            fallback.append(stat.file)
            continue
        if content.count("\n") + 1 > max_lines:
            fallback.append(stat.file)
            continue

        block = annotate_file(stat.file, stat, content, changes.get(stat.file, FileChanges()))
        if len(block) > budget:
            fallback.append(stat.file)
            continue
        budget -= len(block)
        rendered[stat.file] = block

    ordered = [rendered[s.file] for s in files if s.file in rendered]
    return "\n\n".join(ordered), [s.file for s in files if s.file in rendered], sorted(fallback)


@dataclass
class DiffBundle:
    root: Path
    # Human-readable name of the source branch (the one being merged)
    branch: str
    # The ref git actually works with; also handed to the agent's tools
    source_ref: str
    target: str
    base_sha: str
    head: str
    # Source branch differs from the working copy — tools read from git, not disk
    detached: bool
    files: list[DiffStat]
    # Changed files in full with markup — the agent's primary context
    annotated: str
    inlined: list[str]
    # What did not fit goes out as hunks; the rest the agent reads via tools
    fallback: list[str]
    text: str
    truncated: bool
    # Which lines of each changed file the MR touched — the same map the markup
    # above was rendered from, reused to tell a finding about the change from a
    # finding about the code that happened to be nearby. Empty disables that.
    changes: dict[str, FileChanges] = field(default_factory=dict)
    # What the diff introduces that resolves to nothing. Computed once, before
    # the fan-out, so every agent shares it and the prompt prefix stays
    # identical — see resolve.py. None when the pass is switched off.
    references: ReferenceReport | None = None

    @property
    def file_list(self) -> list[str]:
        return [f.file for f in self.files]

    def summary_table(self) -> str:
        if not self.files:
            return "(no changed files)"
        inlined = set(self.inlined)
        rows = []
        for f in self.files:
            note = "" if f.file in inlined else "   [not attached in full]"
            rows.append(f"{f.status:<3} +{f.added:<5} -{f.removed:<5} {f.file}{note}")
        return "\n".join(rows)


def collect(
    root: Path,
    target: str,
    source: str | None = None,
    *,
    context_lines: int,
    max_chars: int,
    excludes: list[str],
    inline_max_lines: int = 600,
    inline_max_total_chars: int = 400_000,
    resolve_references: bool = True,
) -> DiffBundle:
    """Collects the review context for a pair of branches.

    source defaults to the current branch. When given explicitly there is no need
    to check it out: everything is read from git by ref, the working copy is not
    involved.
    """
    resolved_target = resolve_ref(root, target, kind="Target branch")

    if source is None:
        resolved_source = "HEAD"
        branch_name = current_branch(root)
    else:
        resolved_source = resolve_ref(root, source, kind="Source branch")
        branch_name = source

    source_sha = head_sha(root, resolved_source)
    if source_sha == head_sha(root, resolved_target):
        raise GitError(
            f"Source and target branch point at the same commit ({branch_name} and {target})"
        )

    base = merge_base(root, resolved_target, resolved_source)
    files = changed_files(root, base, resolved_source, excludes)
    changes = change_map(root, base, resolved_source, [f.file for f in files])
    annotated, inlined, fallback = build_annotated(
        root, resolved_source, files, changes=changes,
        max_lines=inline_max_lines, max_total_chars=inline_max_total_chars,
    )
    # Hunks are only needed for what was not inlined in full
    text, truncated = diff_text(root, base, resolved_source, fallback, context_lines, max_chars)
    return DiffBundle(
        root=root,
        branch=branch_name,
        source_ref=resolved_source,
        target=resolved_target,
        base_sha=base,
        head=source_sha,
        detached=source_sha != head_sha(root, "HEAD"),
        files=files,
        annotated=annotated,
        inlined=inlined,
        fallback=fallback,
        text=text,
        truncated=truncated,
        changes=changes,
        # Deliberately not filtered by `excludes`: the storyboards and manifests
        # dropped from the context are exactly what has to be searched here.
        references=resolve_check(root, base, resolved_source) if resolve_references else None,
    )
