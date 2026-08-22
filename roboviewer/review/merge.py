"""Collapsing what several reviewers said about the same line.

Eight agents read the same files, so the same problem arrives up to eight times
in different words. Two findings are one when they sit within `MERGE_WINDOW`
lines of each other and name the same code. Identifiers decide that, not prose:
agents describe one defect in words too unalike to match, and two defects on one
line in words too alike to separate.
"""

from __future__ import annotations

import difflib
import re

from ..models import SEVERITY_ORDER, Finding, ItemResult

# Lines this far apart can still be one defect written up twice.
MERGE_WINDOW = 5
# How much of the smaller set of identifiers two findings must share to be
# talking about the same thing.
SUBJECT_OVERLAP = 0.5

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"[a-z][A-Za-z0-9]*[A-Z]")


def merge_findings(results: list[ItemResult]) -> list[Finding]:
    """Collapses duplicates: different checklist items often flag the same line.
    The result is numbered F001, F002… in report order."""
    merged: list[Finding] = []
    for group in _by_file(_reported(results)).values():
        merged.extend(_collapse(group))

    merged.sort(key=lambda f: (SEVERITY_ORDER[f.severity], -f.confidence, f.file, f.line or 0))
    for index, finding in enumerate(merged, start=1):
        finding.id = f"F{index:03d}"
    return merged


def _reported(results: list[ItemResult]) -> list[Finding]:
    """Everything worth carrying forward, each finding knowing who found it."""
    flat: list[Finding] = []
    for result in results:
        for finding in result.findings:
            finding.sources = finding.sources or [result.item_id]
            flat.append(finding)
    return flat


def _by_file(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Grouped by file, which is the only place two findings can be one.

    The window inside a file is applied when comparing, not here: bucketing by
    `line // 5` put a boundary between lines 870 and 871, so adjacent findings
    were never even compared.
    """
    groups: dict[str, list[Finding]] = {}
    for finding in findings:
        groups.setdefault(finding.file, []).append(finding)
    return groups


def _collapse(group: list[Finding]) -> list[Finding]:
    """One place, several wordings: keep one of each problem, the strongest."""
    kept: list[Finding] = []
    for finding in sorted(group, key=lambda f: (f.line is None, f.line or 0)):
        twin = _twin_of(finding, kept)
        if twin is None:
            kept.append(finding)
        elif _stronger(finding, twin):
            finding.sources = _sources(twin, finding)
            kept[kept.index(twin)] = finding
        else:
            twin.sources = _sources(twin, finding)
    return kept


def _twin_of(finding: Finding, kept: list[Finding]) -> Finding | None:
    """The same problem already kept, in other words."""
    return next((k for k in kept if _near(k, finding) and _same_problem(k, finding)), None)


def _near(a: Finding, b: Finding) -> bool:
    """Within the window. A finding about a whole file has no line to compare,
    so it only ever meets other file-level findings."""
    if a.line is None or b.line is None:
        return a.line is None and b.line is None
    return abs(a.line - b.line) <= MERGE_WINDOW


def _same_problem(a: Finding, b: Finding) -> bool:
    """Identifiers first: naming the same symbols is what separates one defect
    written up twice from two defects on one line. Wording decides only when one
    of the two names no code at all.
    """
    ids_a, ids_b = _identifiers(a), _identifiers(b)
    if ids_a and ids_b:
        return len(ids_a & ids_b) / min(len(ids_a), len(ids_b)) >= SUBJECT_OVERLAP
    return (
        _similar(a.title, b.title) > 0.7 or _similar(a.rationale[:240], b.rationale[:240]) > 0.8
    )


def _identifiers(finding: Finding) -> set[str]:
    """Code names in the text: snake_case, camelCase, or followed by a call
    parenthesis. A plain capitalised word is not one — "The" and "Nested" open
    sentences, and matching on those merges unrelated findings.
    """
    text = f"{finding.title} {finding.rationale}"
    names: set[str] = set()
    for match in _WORD.finditer(text):
        word = match.group(0)
        called = text[match.end() : match.end() + 1] == "("
        if "_" in word.strip("_") or _CAMEL.match(word) or called:
            names.add(word)
    return names


def _stronger(finding: Finding, twin: Finding) -> bool:
    """Heavier severity wins; at equal severity, the more confident wording."""
    return (SEVERITY_ORDER[finding.severity], -finding.confidence) < (
        SEVERITY_ORDER[twin.severity],
        -twin.confidence,
    )


def _sources(*findings: Finding) -> list[str]:
    """Every item that reported it, so the report can say who agreed."""
    return sorted({item for finding in findings for item in finding.sources})


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
