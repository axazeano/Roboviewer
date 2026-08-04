"""SARIF 2.1.0 — GitHub Code Scanning, the VS Code viewer, SonarQube.

https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html

Serialized rather than templated: a stray quote in the model's prose would make
the file invalid, and consumers fail silently on that.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import __version__
from ..models import ReviewRun, Severity
from ..view import FindingView, ReviewView, build_view

NAME = "sarif"
FILENAME = "report.sarif"

SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/csd01/schemas/sarif-schema-2.1.0.json"
TOOL_URI = "https://github.com/axazeano/Roboviewer"

# Four levels against our four severities, so blocker and major collapse into
# error; the original goes to properties.
LEVEL: dict[Severity, str] = {
    Severity.BLOCKER: "error",
    Severity.MAJOR: "error",
    Severity.MINOR: "warning",
    Severity.NIT: "note",
}


def render(run: ReviewRun, templates_dir: Path | None = None) -> str:  # noqa: ARG001
    return json.dumps(build(build_view(run)), ensure_ascii=False, indent=2) + "\n"


def build(view: ReviewView) -> dict:
    rules, rule_index = _rules(view)
    return {
        "$schema": SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Roboviewer",
                        "version": __version__,
                        "informationUri": TOOL_URI,
                        "rules": rules,
                    }
                },
                "automationDetails": {"id": f"roboviewer/{view.meta.run_id}"},
                "versionControlProvenance": [
                    {
                        "repositoryUri": Path(view.meta.repo_root).name,
                        "revisionId": view.meta.head_sha,
                        "branch": view.meta.branch,
                    }
                ],
                "invocations": [_invocation(view)],
                # Confirmed only: a rejected finding is a decision that there is
                # no defect, not a suppressed one.
                "results": [_result(f, rule_index) for f in view.findings],
            }
        ],
    }


def _rule_id(finding: FindingView) -> str:
    """The checklist item, which is stable; category is invented per run."""
    return finding.sources[0] if finding.sources else f"category/{finding.category}"


def _rules(view: ReviewView) -> tuple[list[dict], dict[str, int]]:
    """Only the rules actually used — consumers show this list to the user."""
    titles = {item.id: item.title for item in view.items}

    ids: list[str] = []
    for finding in view.findings:
        rule_id = _rule_id(finding)
        if rule_id not in ids:
            ids.append(rule_id)

    rules = [
        {
            "id": rule_id,
            "name": titles.get(rule_id, rule_id),
            "shortDescription": {"text": titles.get(rule_id, rule_id)},
        }
        for rule_id in ids
    ]
    return rules, {rule_id: index for index, rule_id in enumerate(ids)}


def _message(finding: FindingView) -> str:
    parts = [finding.title, finding.rationale]
    if finding.suggestion:
        parts.append(f"Fix: {finding.suggestion}")
    return "\n\n".join(part for part in parts if part)


def _location(finding: FindingView) -> dict:
    physical: dict = {"artifactLocation": {"uri": finding.file.strip().lstrip("./")}}
    # A region without startLine is invalid, so a finding with no line stays
    # attached to the file rather than to an invented first line.
    if finding.line:
        region = {"startLine": finding.line}
        if finding.end_line and finding.end_line >= finding.line:
            region["endLine"] = finding.end_line
        physical["region"] = region
    return {"physicalLocation": physical}


def _result(finding: FindingView, rule_index: dict[str, int]) -> dict:
    rule_id = _rule_id(finding)
    return {
        "ruleId": rule_id,
        "ruleIndex": rule_index[rule_id],
        "level": LEVEL[finding.severity],
        "message": {"text": _message(finding)},
        "locations": [_location(finding)],
        # Versioned so a change in how it is computed does not silently match
        # old runs against new ones.
        "partialFingerprints": {"roboviewerFinding/v1": finding.fingerprint},
        "properties": {
            "severity": finding.severity.value,
            "category": finding.category,
            "confidence": finding.confidence,
            "checklistItems": finding.sources,
            "verdict": finding.verdict,
        },
    }


def _invocation(view: ReviewView) -> dict:
    """Failed and cut-off items belong here, or four results read as a complete
    review. Truncation is a warning rather than an error: the item did produce
    findings, it just never finished looking."""
    invocation: dict = {
        "executionSuccessful": not view.failed_items,
        "startTimeUtc": view.meta.started_at,
    }
    if view.meta.finished_at:
        invocation["endTimeUtc"] = view.meta.finished_at

    notifications = [
        {
            "level": "error",
            "message": {"text": f"Checklist item “{item.title}” failed: {item.error}"},
            "descriptor": {"id": item.id},
        }
        for item in view.failed_items
    ] + [
        {
            "level": "warning",
            "message": {
                "text": (
                    f"Checklist item “{item.title}” hit the turn limit at turn "
                    f"{item.turns} and was made to submit; its aspect was not "
                    f"reviewed to the end."
                )
            },
            "descriptor": {"id": item.id},
        }
        for item in view.truncated_items
    ]
    if notifications:
        invocation["toolExecutionNotifications"] = notifications
    return invocation
