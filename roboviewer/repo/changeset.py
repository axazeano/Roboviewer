"""The change under review, as collected from git.

Two key decisions:

1. We compare from the merge-base, not against the target branch directly.
   `git diff target HEAD` would drag in other people's commits that landed on
   target after the branch point — reviewing those is not our job.

2. The agent gets changed files IN FULL with changed lines marked up, not hunks.
   A diff with a few lines of context is the main source of false positives: a
   guard sitting twenty lines above never makes it into the hunk, and the agent
   reports "no handling here" when in fact there is.

`ChangeSet` is what `collect` returns and what the rest of the review reads:
which branches are compared, which files and lines changed, what the agent is
shown, and what the diff references that resolves to nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import DiffStat
from .annotate import build_annotated
from .diff import FileChanges, change_map, changed_files, diff_text
from .git import GitError, current_branch, merge_base, resolve_ref, rev_parse
from .references import ReferenceReport
from .references import check as check_references


@dataclass(frozen=True)
class ContextBudget:
    """How much text one review is allowed to read.

    The four numbers are one decision, not four: raising the inline budget
    without raising the hunk one only moves where the same context is cut. They
    travel together from the config down to the two renderers, so they arrive
    together.
    """

    context_lines: int
    max_chars: int
    inline_max_lines: int = 600
    inline_max_total_chars: int = 400_000


@dataclass(frozen=True)
class Comparison:
    """What is compared with what."""

    root: Path
    # Human-readable name of the source branch (the one being merged)
    source: str
    # The ref git actually works with; also handed to the agent's tools
    source_ref: str
    target: str
    base_sha: str
    head_sha: str
    # Source branch differs from the working copy — tools read from git, not disk
    detached: bool


@dataclass
class Attachments:
    """What the agent is shown: changed files in full where they fit, hunks for
    the rest, and the rest of the rest it reads via tools."""

    annotated: str
    inlined: list[str]
    # What did not fit in full and goes out as hunks instead
    fallback: list[str]
    hunks: str
    hunks_truncated: bool


@dataclass
class ChangeSet:
    comparison: Comparison
    files: list[DiffStat]
    # Which lines of each changed file the MR touched — the same map the markup
    # was rendered from, reused to tell a finding about the change from a
    # finding about the code that happened to be nearby. Empty disables that.
    lines: dict[str, FileChanges]
    attachments: Attachments
    # What the diff introduces that resolves to nothing. Computed once, before
    # the fan-out, so every agent shares it and the prompt prefix stays
    # identical — see references.py. None when the pass is switched off.
    references: ReferenceReport | None = None

    @property
    def file_list(self) -> list[str]:
        return [f.file for f in self.files]

    def summary_table(self) -> str:
        if not self.files:
            return "(no changed files)"
        inlined = set(self.attachments.inlined)
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
    budget: ContextBudget,
    excludes: list[str],
    resolve_references: bool = True,
) -> ChangeSet:
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

    source_sha = rev_parse(root, resolved_source)
    if source_sha == rev_parse(root, resolved_target):
        raise GitError(
            f"Source and target branch point at the same commit ({branch_name} and {target})"
        )

    base = merge_base(root, resolved_target, resolved_source)
    files = changed_files(root, base, resolved_source, excludes)
    lines = change_map(root, base, resolved_source, [f.file for f in files])
    annotated, inlined, fallback = build_annotated(
        root, resolved_source, files, changes=lines,
        max_lines=budget.inline_max_lines, max_total_chars=budget.inline_max_total_chars,
    )
    # Hunks are only needed for what was not inlined in full
    hunks, truncated = diff_text(
        root, base, resolved_source, fallback, budget.context_lines, budget.max_chars
    )
    return ChangeSet(
        comparison=Comparison(
            root=root,
            source=branch_name,
            source_ref=resolved_source,
            target=resolved_target,
            base_sha=base,
            head_sha=source_sha,
            detached=source_sha != rev_parse(root, "HEAD"),
        ),
        files=files,
        lines=lines,
        attachments=Attachments(
            annotated=annotated,
            inlined=inlined,
            fallback=fallback,
            hunks=hunks,
            hunks_truncated=truncated,
        ),
        # Deliberately not filtered by `excludes`: the storyboards and manifests
        # dropped from the context are exactly what has to be searched here.
        references=check_references(root, base, resolved_source) if resolve_references else None,
    )
