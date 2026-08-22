"""GitLab Code Quality — the merge request widget and inline diff markers.

https://docs.gitlab.com/ci/testing/code_quality/#code-quality-report-format

A subset of the Code Climate format. The file is named as GitLab's own examples
name it, since the path is written by hand in .gitlab-ci.yml anyway.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import ReviewRun, Severity
from ..view import FindingView, ReviewView, build_view

NAME = "codequality"
FILENAME = "gl-code-quality-report.json"

# No `critical`: we have no step between blocker and major.
SEVERITY: dict[Severity, str] = {
    Severity.BLOCKER: "blocker",
    Severity.MAJOR: "major",
    Severity.MINOR: "minor",
    Severity.NIT: "info",
}


def render(run: ReviewRun, templates_dir: Path | None = None) -> str:  # noqa: ARG001
    return json.dumps(build(build_view(run)), ensure_ascii=False, indent=2) + "\n"


def build(view: ReviewView) -> list[dict]:
    return [_entry(finding) for finding in view.findings]


def _entry(finding: FindingView) -> dict:
    return {
        # The only text field there is; the rationale stays in report.md.
        "description": finding.title,
        "check_name": finding.sources[0] if finding.sources else finding.category,
        # Duplicates get collapsed into one entry.
        "fingerprint": finding.fingerprint,
        "severity": SEVERITY[finding.severity],
        "location": {
            "path": finding.file,
            # Required and numeric; without it GitLab drops the entry entirely.
            "lines": {"begin": finding.line or 1},
        },
    }
