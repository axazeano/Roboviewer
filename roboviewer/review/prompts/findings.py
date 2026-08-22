"""Findings as a judge sees them.

Structure, not prose, so it stays in code rather than in a template: the fields
of a finding laid out for a model to check, and the one-line roster a per-finding
judge is given so it can still recognise a duplicate without seeing the other
findings in full.
"""

from __future__ import annotations

from ...models import Finding

ROSTER_BLOCK = """\
# The other findings in this review

Listed so you can recognise a duplicate. Judge only the finding above; a
`duplicate` verdict is for the same problem already reported under a LOWER id.

{roster}"""


def render_finding(finding: Finding, note: str | None = None) -> str:
    """A finding as the judge sees it. `note` is the verification a previous pass
    already did, shown only to a judge that comes after one.

    The reviewer's severity and confidence are deliberately absent. Both are
    guesses made before anything was verified, by an agent that saw one aspect
    of the diff and had only its own findings to rank against — and a judge
    shown them follows them. The same claim about `fastjson.c:71` was rejected
    when the reviewer hedged at 0.30 and confirmed as major when another
    reviewer wrote it up confidently; the code had not changed.
    """
    lines = [
        f"## {finding.id} — {finding.title}",
        f"- File: `{finding.location}`",
        f"- Category: {finding.category}",
        f"- Found by: {', '.join(finding.sources) or '—'}",
        f"- Rationale: {finding.rationale}",
    ]
    if finding.suggestion:
        lines.append(f"- Suggestion: {finding.suggestion}")
    if note:
        lines.append(f"- Verified: {note}")
    return "\n".join(lines)


def render_roster(others: list[Finding]) -> str:
    """One line per other finding. Enough to spot a duplicate, cheap enough to
    repeat in every per-finding pass — and severity is not part of that, for
    the same reason it is missing from the finding itself."""
    if not others:
        return ""
    lines = [f"- {f.id} `{f.location}` — {f.title}" for f in sorted(others, key=lambda f: f.id)]
    return ROSTER_BLOCK.format(roster="\n".join(lines))
