"""The merge request as the agent sees it: the context block every prompt opens with.

Structure rather than wording — the header, the list of changed files, the files
themselves with markup and the legend for it, the hunks for what did not fit,
and the result of the reference pre-pass. It is assembly over `ChangeSet`
fields, which is why it stays in code while the eight prompt texts live in
markdown; and the legend has to describe exactly the markup `repo.annotate`
emits, since the agent reads line numbers off it.
"""

from __future__ import annotations

from ...repo import ChangeSet
from ...repo.annotate import MARKER_ADDED, MARKER_CONTEXT, MARKER_REMOVED
from ...repo.references import ReferenceReport

ANNOTATION_LEGEND = (
    "Format: line number in the new version of the file, marker, code.\n"
    f"   12{MARKER_CONTEXT}code   — line unchanged in this MR, shown for context\n"
    f"   13{MARKER_ADDED}code   — line added or changed in this MR\n"
    f"     {MARKER_REMOVED}code   — line removed in this MR "
    "(absent from the new version, so it has no number)"
)

CONTEXT_BLOCK = """\
# Merge request context

Repository: {repo}
Branch: {branch} → {target}
Merge base: {base_sha}

## Changed files
```
{files}
```

## Changed files in full

{legend}

{annotated}
{fallback_block}{references_block}"""

# Output of the resolution pre-pass. Two sections, worded differently on
# purpose: the resource misses are search results, the symbol list is a lead the
# agent has to finish checking. Saying so is what keeps it from being quoted as
# proof — an unverified claim costs the author more than a missing one.
REFERENCES_BLOCK = """
# Reference check

Run over the whole tree before this review, including files not attached above
(storyboards, build manifests, strings). What the two sections below are for
differs, and the rule about the build decides which is which: a reference that
fails only at run time is a finding, one a failed build would already show is
not. Report in your own words, and only after you have looked at the code.
{sections}"""

RESOURCE_SECTION = """
## References that resolve to nothing ({count})

Searched and absent. These are search results, not guesses.

No compiler looks at any of them: the code below builds, ships, and fails when
the screen opens. That makes them findings, and the search behind each one is
already done — confirm the reference is really introduced by this change, then
report it.

{rows}"""

SYMBOL_SECTION = """
## Identifiers with no definition in this repository ({count})

Introduced by this diff, used in a position that has to resolve, and found
nowhere outside the files the diff touches, with no declaration anywhere.

Context, not a list of findings. This list cannot see your dependencies, so
framework and SDK symbols land in it legitimately and are NOT problems. It is
here so that an unfamiliar name does not send you down the wrong path: where
the project is compiled, a name resolving nowhere is the build's to report and
not yours, and no turn of yours should go to it. Report an entry only where
nothing gets built — a name resolved at run time, spelled in a string, reached
by reflection or by a selector.

{rows}"""

FALLBACK_BLOCK = """
## Files not attached in full

Too large, or deleted — only the changed fragments are below. Read the full
contents with `read_file`, and the state before the changes with `git_show`.

```diff
{diff}
```
"""


def context_block(changes: ChangeSet) -> str:
    shown = changes.attachments
    fallback = ""
    if shown.fallback and shown.hunks:
        fallback = FALLBACK_BLOCK.format(diff=shown.hunks)
        if shown.hunks_truncated:
            fallback += "\n> Fragments were truncated by size; read the rest with the tools.\n"

    return CONTEXT_BLOCK.format(
        repo=changes.comparison.root.name,
        branch=changes.comparison.source,
        target=changes.comparison.target,
        base_sha=changes.comparison.base_sha[:12],
        files=changes.summary_table(),
        legend=ANNOTATION_LEGEND,
        annotated=shown.annotated or "(no file was attached in full)",
        fallback_block=fallback,
        references_block=references_block(changes.references),
    )


def references_block(report: ReferenceReport | None) -> str:
    """The pre-pass result. Absent when it did not run — an empty section would
    read as "nothing was found", which is a different claim."""
    if report is None or report.empty:
        return ""

    sections: list[str] = []
    if report.resource_misses:
        # Grouped by question and file: forty keys missing from one file is one
        # fact stated once, not forty repetitions of the same sentence.
        groups: dict[tuple[str, str], list[str]] = {}
        for _, question, value, path in report.resource_misses:
            groups.setdefault((question, path), []).append(value)
        rows = "\n".join(
            f"- {question}\n  in `{path}`: " + ", ".join(f"`{v}`" for v in values)
            for (question, path), values in groups.items()
        )
        sections.append(
            RESOURCE_SECTION.format(count=len(report.resource_misses), rows=rows)
        )

    if report.unresolved_symbols:
        rows = "\n".join(
            f"- `{name}` — referenced in {', '.join(f'`{p}`' for p in paths[:3])}"
            for name, paths in sorted(report.unresolved_symbols.items())
        )
        if report.symbols_truncated:
            # Never let a cap read as coverage
            rows += f"\n- [... {report.symbols_truncated} more, not listed ...]"
        sections.append(
            SYMBOL_SECTION.format(count=len(report.unresolved_symbols), rows=rows)
        )

    return REFERENCES_BLOCK.format(sections="\n".join(sections))
