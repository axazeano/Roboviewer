"""SARIF and GitLab Code Quality.

Interchange rather than documents: a consumer given a malformed file just shows
nothing. So the required fields and the scale mappings are what get checked.
"""

from __future__ import annotations

import json

import pytest

from roboviewer import renders
from roboviewer.models import Finding, Severity
from roboviewer.report import render_report
from roboviewer.view import build_view

from .test_report import empty_run, full_run

# ------------------------------------------------------------------ fingerprint


def test_fingerprint_survives_a_line_shift() -> None:
    before = build_view(full_run()).findings[0].fingerprint

    moved = full_run()
    for finding in moved.findings:
        if finding.line:
            finding.line += 40
    assert build_view(moved).findings[0].fingerprint == before


def test_fingerprints_are_unique_within_a_report() -> None:
    # Equal fingerprints collapse into one entry in GitLab
    run = full_run()
    run.findings.append(run.findings[0].model_copy(update={"id": "F7", "line": 200}))

    prints = [f.fingerprint for f in build_view(run).findings]
    assert len(prints) == len(set(prints))


def test_fingerprint_ignores_wording_whitespace() -> None:
    run = full_run()
    run.findings[0].title = "  Половина   ширины\nвместо обрезки "
    assert build_view(run).findings[0].fingerprint == build_view(full_run()).findings[0].fingerprint


# ------------------------------------------------------------------------ SARIF


@pytest.fixture
def sarif() -> dict:
    return json.loads(render_report(full_run(), "sarif"))


def test_sarif_has_the_shape_the_spec_requires(sarif: dict) -> None:
    assert sarif["version"] == "2.1.0"
    assert sarif["$schema"].endswith("sarif-schema-2.1.0.json")
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "Roboviewer"

    for result in sarif["runs"][0]["results"]:
        assert result["level"] in {"none", "note", "warning", "error"}
        assert result["message"]["text"]
        assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]


def test_sarif_reports_only_what_the_author_sees(sarif: dict) -> None:
    verdicts = {r["properties"]["verdict"] for r in sarif["runs"][0]["results"]}

    assert "false_positive" not in verdicts and "duplicate" not in verdicts
    assert len(sarif["runs"][0]["results"]) == 4


@pytest.mark.parametrize(
    ("severity", "level"),
    [
        (Severity.BLOCKER, "error"),
        (Severity.MAJOR, "error"),
        (Severity.MINOR, "warning"),
        (Severity.NIT, "note"),
    ],
)
def test_sarif_maps_severity_onto_a_poorer_scale(severity: Severity, level: str) -> None:
    run = full_run()
    run.findings[0].severity = severity
    result = json.loads(render_report(run, "sarif"))["runs"][0]["results"][0]

    assert result["level"] == level
    assert result["properties"]["severity"] == severity.value


def test_sarif_omits_the_region_when_there_is_no_line(sarif: dict) -> None:
    # A region without startLine is invalid
    without_line = [r for r in sarif["runs"][0]["results"] if "Нет теста" in r["message"]["text"]][0]
    assert "region" not in without_line["locations"][0]["physicalLocation"]


def test_sarif_rules_are_declared_for_every_result(sarif: dict) -> None:
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]
    ids = [rule["id"] for rule in rules]

    assert len(ids) == len(set(ids))
    for result in sarif["runs"][0]["results"]:
        assert result["ruleId"] in ids
        assert rules[result["ruleIndex"]]["id"] == result["ruleId"]
        assert rules[result["ruleIndex"]]["shortDescription"]["text"]


def test_sarif_admits_that_a_checklist_item_crashed(sarif: dict) -> None:
    invocation = sarif["runs"][0]["invocations"][0]

    assert invocation["executionSuccessful"] is False
    assert len(invocation["toolExecutionNotifications"]) == 1
    assert "Concurrency" in invocation["toolExecutionNotifications"][0]["message"]["text"]


def test_sarif_of_a_clean_run_is_successful_and_empty() -> None:
    run = json.loads(render_report(empty_run(), "sarif"))["runs"][0]

    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []
    assert run["invocations"][0]["executionSuccessful"] is True


def test_a_finding_outside_the_checklist_still_gets_a_rule() -> None:
    run = empty_run()
    run.findings = [
        Finding(id="F1", file="a.swift", line=3, category="style", title="Т", rationale="Р")
    ]
    sarif = json.loads(render_report(run, "sarif"))["runs"][0]

    assert sarif["results"][0]["ruleId"] == "category/style"
    assert sarif["tool"]["driver"]["rules"][0]["id"] == "category/style"


# ----------------------------------------------------------------- Code Quality


def test_codequality_carries_every_field_gitlab_requires() -> None:
    entries = json.loads(render_report(full_run(), "codequality"))

    assert len(entries) == 4
    for entry in entries:
        assert entry["description"]
        assert entry["fingerprint"]
        assert entry["severity"] in {"info", "minor", "major", "critical", "blocker"}
        assert entry["location"]["path"]
        assert isinstance(entry["location"]["lines"]["begin"], int)


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (Severity.BLOCKER, "blocker"),
        (Severity.MAJOR, "major"),
        (Severity.MINOR, "minor"),
        (Severity.NIT, "info"),
    ],
)
def test_codequality_severity_matches_gitlabs_scale(severity: Severity, expected: str) -> None:
    run = full_run()
    run.findings[0].severity = severity
    assert json.loads(render_report(run, "codequality"))[0]["severity"] == expected


def test_codequality_falls_back_to_the_first_line_without_one() -> None:
    # begin is required and numeric; without it the entry is dropped
    entries = json.loads(render_report(full_run(), "codequality"))
    entry = [e for e in entries if e["description"] == "Нет теста на пустой reply"][0]

    assert entry["location"]["lines"]["begin"] == 1


def test_codequality_of_a_clean_run_is_an_empty_array() -> None:
    assert json.loads(render_report(empty_run(), "codequality")) == []


# ------------------------------------------------------------------------ both


@pytest.mark.parametrize("fmt", ["sarif", "codequality"])
def test_machine_formats_need_no_template(fmt: str) -> None:
    assert not hasattr(renders.resolve(fmt), "TEMPLATE")


@pytest.mark.parametrize("fmt", ["sarif", "codequality"])
def test_machine_formats_survive_markup_in_the_model_text(fmt: str) -> None:
    run = full_run()
    run.findings[0].title = 'Кавычка " и <тег> и \\ и перевод\nстроки'

    assert json.loads(render_report(run, fmt))  # parses, so the JSON is valid


def test_both_formats_are_offered_by_name() -> None:
    assert {"sarif", "codequality"} <= set(renders.known())


def test_file_names_follow_what_the_consumer_expects() -> None:
    assert renders.resolve("sarif").FILENAME == "report.sarif"
    assert renders.resolve("codequality").FILENAME == "gl-code-quality-report.json"
