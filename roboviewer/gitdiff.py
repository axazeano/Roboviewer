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
        raise GitError(f"{start} не внутри git-репозитория") from exc
    return Path(out.strip())


def current_branch(root: Path) -> str:
    name = _git(["rev-parse", "--abbrev-ref", "HEAD"], root).strip()
    return name if name != "HEAD" else _git(["rev-parse", "--short", "HEAD"], root).strip()


def resolve_ref(root: Path, ref: str, *, kind: str = "Ветка") -> str:
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
    raise GitError(f"{kind} не найдена ни как '{ref}', ни как 'origin/{ref}'")


def merge_base(root: Path, target: str, source: str = "HEAD") -> str:
    return _git(["merge-base", target, source], root).strip()


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
        return text[:max_chars] + "\n\n[... дифф усечён; недостающее читай через тулы ...]", True
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

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

MARKER_CONTEXT = "   | "
MARKER_ADDED = " + | "
MARKER_REMOVED = " - | "

ANNOTATION_LEGEND = """\
Формат: номер строки в новой версии файла, маркер, код.
   12   | код   — строка не менялась в этом MR, дана для контекста
   13 + | код   — строка добавлена или изменена в этом MR
      - | код   — строка удалена в этом MR (в новой версии её нет, номера тоже нет)\
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
    result: dict[str, FileChanges] = {}
    current: FileChanges | None = None
    new_line = 0
    in_header = False

    for raw in out.splitlines():
        if raw.startswith("diff --git "):
            current, in_header = None, True
            continue
        if in_header:
            # File header: index / mode / similarity / --- / +++
            if raw.startswith("+++ "):
                path = raw[4:].strip()
                if path == "/dev/null":
                    current = None
                else:
                    key = path[2:] if path.startswith(("a/", "b/")) else path
                    current = result.setdefault(key, FileChanges())
                continue
            if not raw.startswith("@@"):
                continue
            in_header = False  # hunk bodies follow; '---'/'+++' there is code, not a header

        hunk = _HUNK_RE.match(raw)
        if hunk:
            new_line = int(hunk.group(1))
            continue
        if current is None or not raw:
            continue
        if raw[0] == "+":
            current.added.add(new_line)
            new_line += 1
        elif raw[0] == "-":
            current.removed_before.setdefault(new_line, []).append(raw[1:])
        # "\ No newline at end of file" and friends are ignored

    return result


def looks_binary(text: str) -> bool:
    return "\x00" in text[:4000]


def annotate_file(path: str, stat: DiffStat, content: str, changes: FileChanges) -> str:
    lines = content.splitlines()
    header = f"===== {path} [{stat.status}, +{stat.added}/-{stat.removed}, {len(lines)} строк] ====="
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
    base: str,
    source: str,
    files: list[DiffStat],
    *,
    max_lines: int,
    max_total_chars: int,
) -> tuple[str, list[str], list[str]]:
    """Renders changed files in full, with markup.

    Returns (text, files inlined in full, files that did not fit).
    The budget is handed out starting from the most heavily changed files: if it
    cannot cover everything, the ones with more edits should be the ones inlined.
    """
    changes = change_map(root, base, source, [f.file for f in files])
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

    @property
    def file_list(self) -> list[str]:
        return [f.file for f in self.files]

    def summary_table(self) -> str:
        if not self.files:
            return "(изменённых файлов нет)"
        inlined = set(self.inlined)
        rows = []
        for f in self.files:
            note = "" if f.file in inlined else "   [целиком не приложен]"
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
) -> DiffBundle:
    """Collects the review context for a pair of branches.

    source defaults to the current branch. When given explicitly there is no need
    to check it out: everything is read from git by ref, the working copy is not
    involved.
    """
    resolved_target = resolve_ref(root, target, kind="Целевая ветка")

    if source is None:
        resolved_source = "HEAD"
        branch_name = current_branch(root)
    else:
        resolved_source = resolve_ref(root, source, kind="Исходная ветка")
        branch_name = source

    source_sha = head_sha(root, resolved_source)
    if source_sha == head_sha(root, resolved_target):
        raise GitError(
            f"Исходная и целевая ветка указывают на один коммит ({branch_name} и {target})"
        )

    base = merge_base(root, resolved_target, resolved_source)
    files = changed_files(root, base, resolved_source, excludes)
    annotated, inlined, fallback = build_annotated(
        root, base, resolved_source, files,
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
    )
