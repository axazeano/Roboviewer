"""Changed files rendered in full, with the changed lines marked up.

This is the agent's primary context, and the reason for it is in the package
docstring: a diff with a few lines of context is the main source of false
positives. The markers below are what the prompt's legend describes — see
`review.prompts.context` — so the two have to move together.
"""

from __future__ import annotations

from pathlib import Path

from ..models import DiffStat
from .diff import FileChanges
from .git import looks_binary, show_file

MARKER_CONTEXT = "   | "
MARKER_ADDED = " + | "
MARKER_REMOVED = " - | "


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
        content = show_file(root, source, stat.file)
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
