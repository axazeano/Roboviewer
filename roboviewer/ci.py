"""What the pipeline environment already knows about the branches under review.

Under GitLab or GitHub nobody types the target branch: the job already has a
variable naming it, and repeating that in .gitlab-ci.yml is one more place to
get it wrong. So a missing target argument falls back to the variable.

The source deliberately does not: it stays whatever the runner checked out. The
branch named in the variables often has no local ref at all — GitHub checks out
a detached merge commit, GitLab a detached sha — so resolving it would fail
exactly where HEAD, which is the code the pipeline is testing, works.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Merge-request variables only. CI_COMMIT_BRANCH and GITHUB_REF_NAME are set on
# every pipeline including plain branch builds, where there is no target branch
# to speak of and guessing one would review against the wrong base.
FORGES: tuple[tuple[str, str], ...] = (
    ("GitLab CI", "CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
    ("GitHub Actions", "GITHUB_BASE_REF"),
)


@dataclass(frozen=True)
class Environment:
    """Named so the run can say where its target came from — a review against a
    branch nobody chose is worth one line in the log."""

    name: str
    target: str
    variable: str


def detect(environ: Mapping[str, str] | None = None) -> Environment | None:
    env = os.environ if environ is None else environ
    for name, variable in FORGES:
        target = env.get(variable, "").strip()
        if target:
            return Environment(name=name, target=target, variable=variable)
    return None
