"""A finished run as what would be posted: comments on lines, and a body.

Forge-agnostic on purpose. What a review of a merge request consists of — some
remarks against lines, one piece of prose about the whole thing — is the same on
every forge, and only the request that carries it differs. So this decides what
is said and `github` decides how it is sent, which is also why the decision can
be tested without a network and printed by a dry run.

The split between the two is the whole point. A forge can only anchor a comment
to a line its diff actually carries, but the scope gate keeps a finding within a
few lines of a changed one — a finding naming the declaration just above the
edit is a good finding with nowhere to hang. Those go into the body. Dropping
them would make the tool quietly report less through a pull request than it
writes to disk, and a finding the author cannot see is one that was never made.
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

    `unanchored` is how many findings ended up in the body for want of a line —
    counted rather than recomputed, because the console says it and the body
    already says it too.
    """

    body: str
    comments: list[LineComment] = field(default_factory=list)
    unanchored: int = 0

    @property
    def findings(self) -> int:
        return len(self.comments) + self.unanchored


def compose(run: ReviewRun, commentable: Mapping[str, Set[int]]) -> Draft:
    """A run and the lines a forge can hang a comment on → what to post.

    Only what a report shows: a finding the judge threw out is a decision that
    there is no defect, and one pointing outside the change is about code this
    merge request never touched. Neither belongs in somebody's pull request.
    """
    anchored: list[LineComment] = []
    loose: list[Finding] = []
    for finding in _ordered(run.confirmed()):
        line = finding.line
        if line is not None and line in commentable.get(finding.file, frozenset()):
            anchored.append(LineComment(file=finding.file, line=line, body=_comment(finding)))
        else:
            loose.append(finding)
    return Draft(
        body=_body(run, len(anchored), loose),
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


def _body(run: ReviewRun, anchored: int, loose: list[Finding]) -> str:
    parts = [f"## {SIGNATURE.capitalize()}", ""]
    if run.judge_summary:
        parts += [run.judge_summary, ""]
    parts += [_tally(anchored, loose), ""]
    if loose:
        parts += [
            "### Not on a changed line",
            "",
            "These are about the change but point at lines the diff does not carry, "
            "so there is nowhere to hang a comment.",
            "",
        ]
        parts += [_entry(finding) for finding in loose]
    parts.append(_footer(run))
    return "\n".join(parts)


def _tally(anchored: int, loose: list[Finding]) -> str:
    total = anchored + len(loose)
    if not total:
        return "No findings left standing."
    counted = f"**{total} finding{'s' if total != 1 else ''}**"
    if not loose:
        return f"{counted}, each on the line it is about."
    if not anchored:
        return f"{counted}, none of which sits on a line of the diff — all are below."
    return f"{counted}: {anchored} on the diff, {len(loose)} below."


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

    Not which model wrote it: a merge request is often public and the name of a
    model can be somebody's corporate infrastructure, the same reason the job
    that runs this keeps it a secret. Whoever wants it has the run on disk.
    """
    return f"<sub>{SIGNATURE} · {run.branch} into {run.target} · run {run.run_id}</sub>"
