"""Is a finding about the change, or about the code around it?

Reviewers are given changed files in full — which is what stops them inventing
missing handling that sits twenty lines above — and the cost of that is a
standing temptation to report the untouched 98% of the file. The gate is line
arithmetic over the same map the markup was rendered from, so the gate and the
prompt cannot disagree about what "changed" means, and it knows nothing about
languages: a changed line is a changed line in Swift, Go and YAML alike.
"""

from __future__ import annotations

from ..models import repo_path
from ..repo.diff import FileChanges


def in_scope(
    lines: dict[str, FileChanges], file: str, line: int | None, margin: int
) -> bool:
    """Does a finding point at what this MR did?

    A removal is an anchor too — deleting a method from a protocol is a change,
    and the only line left to point at is the neighbour that survived. Hence the
    margin, which is also what covers a finding that names the declaration a few
    lines above the edit. It is a heuristic and the report keeps what it drops.
    """
    if not lines:
        return True  # no map, no gate — never silently drop everything

    entry = lines.get(repo_path(file))
    if entry is None:
        return False  # a file this MR never touched
    if line is None:
        return True  # about the file as a whole, which the MR did touch

    anchors = entry.added | set(entry.removed_before)
    return any(abs(line - anchor) <= margin for anchor in anchors)
