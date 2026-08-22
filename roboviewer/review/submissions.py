"""What the agents hand back, turned into models.

A terminal tool's payload is model output: a dict of whatever shape the model
felt like emitting. Everything that reads one goes through here, so there is one
place that decides what a malformed entry costs — the entry, never the item or
the run it belongs to.
"""

from __future__ import annotations

from typing import Any

from ..models import Finding, Verdict


def findings_from_payload(payload: dict[str, Any], item_id: str) -> tuple[str, list[Finding]]:
    """(the item's summary, its findings) — each finding stamped with the item
    that found it, each malformed entry skipped on its own."""
    summary = str(payload.get("summary", "")).strip()
    raw_items = payload.get("findings") or []
    findings: list[Finding] = []
    if not isinstance(raw_items, list):
        return summary, findings
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            finding = Finding.model_validate({**raw, "sources": [item_id]})
        except Exception:  # noqa: BLE001 — one malformed entry must not sink the whole item
            continue
        findings.append(finding)
    return summary, findings


def verdicts_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Verdict]]:
    """(the judge's summary, its verdicts by finding id) from a pass that ruled
    on several findings at once."""
    summary = str(payload.get("summary", "")).strip()
    verdicts: dict[str, Verdict] = {}
    for raw in payload.get("verdicts") or []:
        if not isinstance(raw, dict):
            continue
        try:
            verdict = Verdict.model_validate(raw)
        except Exception:  # noqa: BLE001
            continue
        verdicts[verdict.finding_id] = verdict
    return summary, verdicts


def verdict_from_payload(payload: dict[str, Any], finding_id: str) -> Verdict | None:
    """One verdict, from a pass that was told about exactly one finding. The id is
    ours, not the model's: echoing it back would only be a chance to get it wrong."""
    try:
        return Verdict.model_validate({**payload, "finding_id": finding_id})
    except Exception:  # noqa: BLE001 — a malformed verdict must not sink the finding
        return None
