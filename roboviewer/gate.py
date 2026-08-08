"""Whether a finished run should fail the pipeline, and with which exit code.

A CI job is only useful if it can go red, and the exit code is the whole of what
a runner reads. Which findings are worth going red over is a judgement about the
run rather than a step in it, so it lives here and `cli` keeps only the order of
the steps.

Four codes rather than two, because "the review found a blocker" and "an agent
crashed" call for different reactions: one is fixed in the branch, the other
rerun. A blocker wins over an incomplete run — it is the one that names what to
do next, and the job goes red either way.
"""

from __future__ import annotations

from .models import SEVERITY_ORDER, Finding, ReviewRun, Severity

OK = 0
# Findings at or above the threshold. The review itself went fine.
FINDINGS = 1
# The tool could not do its job: bad config, git error, nothing to render into.
# Raised by `cli`, named here so all four codes read in one place.
SETUP = 2
# The review ran but a checklist item failed, so its aspect went unreviewed.
INCOMPLETE = 3

NEVER = "never"
# Worst first, so a threshold names a prefix of this list. `never` last: it is
# not a severity, it is the absence of a gate.
THRESHOLDS: tuple[str, ...] = (
    *(severity.value for severity in sorted(SEVERITY_ORDER, key=lambda s: SEVERITY_ORDER[s])),
    NEVER,
)


def blocking(run: ReviewRun, threshold: str) -> list[Finding]:
    """Confirmed findings at or above the threshold.

    Confirmed and in scope: a rejected finding is a decision that there is no
    defect, and an out-of-scope one is about code this MR never touched. Failing
    a pipeline on either would make the gate untrustworthy, which is the one
    thing a gate cannot afford to be.
    """
    if threshold == NEVER:
        return []
    limit = SEVERITY_ORDER[Severity(threshold)]
    return [f for f in run.confirmed() if SEVERITY_ORDER[f.severity] <= limit]


def exit_code(run: ReviewRun, threshold: str) -> int:
    if blocking(run, threshold):
        return FINDINGS
    if any(item.status == "failed" for item in run.items):
        return INCOMPLETE
    return OK
