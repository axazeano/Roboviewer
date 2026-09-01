"""A finished run as what would be posted: comments on lines, and a body.

Forge-agnostic: every forge carries the same two things, and only the request
differs.

A forge anchors a comment only to a line its diff carries, while the scope gate
keeps a finding within a few lines of a changed one. Findings outside the diff
go into the body rather than being dropped.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass, field

from ..models import SEVERITY_LABEL, SEVERITY_ORDER, Finding, ReviewRun

# What a body is signed with, so a reader knows what left it there.
SIGNATURE = "roboviewer"


@dataclass(frozen=True)
class LineComment:
    """One finding, against one line of one file in the new version."""

    file: str
    line: int
    body: str


@dataclass(frozen=True)
class Draft:
    """What a forge is asked to post: the prose, and the remarks on lines.

    `body_alone` is the same review with every finding written out, for a forge
    that takes the prose and refuses the anchors. Without it the fallback would
    post a body naming only the findings that were never going to be comments.

    `unanchored` is how many findings ended up in the body for want of a line.
    """

    body: str
    body_alone: str
    comments: list[LineComment] = field(default_factory=list)
    unanchored: int = 0

    @property
    def findings(self) -> int:
        return len(self.comments) + self.unanchored


def compose(run: ReviewRun, commentable: Mapping[str, Set[int]]) -> Draft:
    """A run and the lines a forge can hang a comment on → what to post.

    Only what a report shows: findings the judge rejected and findings outside
    the change are left out.
    """
    ordered = _ordered(run.confirmed())
    anchored: list[LineComment] = []
    loose: list[Finding] = []
    for finding in ordered:
        line = finding.line
        if line is not None and line in commentable.get(finding.file, frozenset()):
            anchored.append(LineComment(file=finding.file, line=line, body=_comment(finding)))
        else:
            loose.append(finding)
    return Draft(
        body=_body(run, len(anchored), loose),
        body_alone=_body(run, 0, ordered, refused=True),
        comments=anchored,
        unanchored=len(loose),
    )


def _ordered(findings: list[Finding]) -> list[Finding]:
    """Worst first, then by where they are, so the body reads in one order and
    the comments were decided in the same one."""
    return sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line or 0))


def _comment(finding: Finding) -> str:
    parts = [f"**{SEVERITY_LABEL[finding.severity]} — {finding.title}**", "", finding.rationale]
    if finding.suggestion:
        parts += ["", f"**Suggestion:** {finding.suggestion}"]
    return "\n".join(parts)


def _body(
    run: ReviewRun, anchored: int, listed: list[Finding], *, refused: bool = False
) -> str:
    parts = [f"## {SIGNATURE.capitalize()}", ""]
    if run.judge_summary:
        parts += [run.judge_summary, ""]
    parts += [_tally(anchored, listed, refused), ""]
    if listed:
        parts += _heading(refused)
        parts += [_entry(finding) for finding in listed]
    parts.append(_footer(run))
    return "\n".join(parts)


def _heading(refused: bool) -> list[str]:
    if refused:
        return [
            "### Findings",
            "",
            "The forge would not take these on the lines they name, so they are here.",
            "",
        ]
    return [
        "### Not on a changed line",
        "",
        "These are about the change but point at lines the diff does not carry, "
        "so there is nowhere to hang a comment.",
        "",
    ]


def _tally(anchored: int, listed: list[Finding], refused: bool = False) -> str:
    total = anchored + len(listed)
    if not total:
        return "No findings left standing."
    counted = f"**{total} finding{'s' if total != 1 else ''}**"
    if refused:
        return f"{counted}, all written out below."
    if not listed:
        return f"{counted}, each on the line it is about."
    if not anchored:
        return f"{counted}, none of which sits on a line of the diff — all are below."
    return f"{counted}: {anchored} on the diff, {len(listed)} below."


def _entry(finding: Finding) -> str:
    lines = [
        f"#### `{finding.location}` — {SEVERITY_LABEL[finding.severity]}: {finding.title}",
        "",
        finding.rationale,
        "",
    ]
    if finding.suggestion:
        lines += [f"**Suggestion:** {finding.suggestion}", ""]
    return "\n".join(lines)


def _footer(run: ReviewRun) -> str:
    """What left the remark and what it was looking at.

    Not the model: a merge request is often public, and the job that posts one
    holds the model name as a secret. It stays in the run on disk.
    """
    return f"<sub>{SIGNATURE} · {run.branch} into {run.target} · run {run.run_id}</sub>"
