"""What a run writes down about itself: one JSON object per line.

A line at a time, flushed as it is written, because the runs whose behaviour is
most in question are the ones that die halfway. Whatever happened before the
process ended is already on disk.

Records carry what was asked for and how much came back — never the answer. A
tool call returns repository content, and a log that kept it would be a second
copy of the repository, growing with what the agent read rather than with what
it did.

The field names are short (`a` for the agent, `n` for the turn) because they
repeat on every line of a file that has thousands, and nothing reads them by
hand — `view.py` turns them back into words.

The tool knows none of this. It reports what its agents did through
`roboviewer.observe` and keeps nothing; the file below is this package's
business alone.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from roboviewer.models import Usage
from roboviewer.observe import AgentKind

# The log itself and the page rendered from it, both inside the run directory.
LOG = "trace.jsonl"
PAGE = "trace.html"

AgentStatus = Literal["ok", "truncated", "failed"]


class FileRecord(BaseModel):
    """A changed file, so the log can say on its own what the run was given."""

    file: str
    status: str = "M"
    added: int = 0
    removed: int = 0


class ItemRecord(BaseModel):
    id: str
    title: str


class RunRecord(BaseModel):
    """The first line of the log: which run this is and what it set out to do."""

    t: Literal["run"] = "run"
    run_id: str
    branch: str = ""
    target: str = ""
    base_sha: str = ""
    head_sha: str = ""
    model: str = ""
    started_at: str = ""
    files: list[FileRecord] = Field(default_factory=list)
    # In checklist order, which is the order the page groups by
    items: list[ItemRecord] = Field(default_factory=list)


class BlobRecord(BaseModel):
    """A prompt, written once and referred to by hash.

    The judge asks one system prompt of every finding it verifies, so a run of
    thirty findings would otherwise carry thirty copies of it.
    """

    t: Literal["blob"] = "blob"
    h: str
    text: str


class AgentRecord(BaseModel):
    """One agent starting: a checklist item, or one of the judge's passes."""

    t: Literal["agent"] = "agent"
    a: str
    kind: AgentKind
    title: str
    item_id: str = ""
    system: str = ""
    prompt: str = ""
    max_turns: int = 0


class TurnRecord(BaseModel):
    """What the model said on this turn, and what the turn cost.

    Two texts, because a reasoning model uses two: `text` is what it said out
    loud, `thinking` what it worked out in the field beside it. On most turns of
    such a model the first is empty and the second is the whole reply.
    """

    t: Literal["turn"] = "turn"
    a: str
    n: int
    text: str = ""
    thinking: str = ""
    usage: Usage = Field(default_factory=Usage)


class CallRecord(BaseModel):
    """One tool call: what was asked for, and how much came back.

    `args` is the model's own argument object — a path, a pattern, a line range.
    It is model output rather than repository content, and it is the whole point
    of the record: what the agent went looking for.
    """

    t: Literal["call"] = "call"
    a: str
    n: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    chars: int = 0
    # Lines for a read or entries for a listing; matches for a search. Which of
    # the two a tool answers with is the tool's business, so both are optional.
    lines: int | None = None
    hits: int | None = None
    error: bool = False
    seconds: float = 0.0


class SubmittedFinding(BaseModel):
    severity: str = ""
    location: str = ""
    title: str = ""


class SubmittedVerdict(BaseModel):
    finding_id: str = ""
    verdict: str = ""
    reason: str = ""


class OutcomeRecord(BaseModel):
    """How the agent's run ended — the verdict the runner came back with."""

    t: Literal["outcome"] = "outcome"
    a: str
    status: AgentStatus
    turns: int = 0
    duration_s: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None
    summary: str = ""
    findings: list[SubmittedFinding] = Field(default_factory=list)
    verdicts: list[SubmittedVerdict] = Field(default_factory=list)


TYPES: dict[str, type[BaseModel]] = {
    "run": RunRecord,
    "blob": BlobRecord,
    "agent": AgentRecord,
    "turn": TurnRecord,
    "call": CallRecord,
    "outcome": OutcomeRecord,
}


def dumps(record: BaseModel) -> str:
    """One record as the line it is stored on."""
    return record.model_dump_json(exclude_none=False)


def loads(line: str) -> BaseModel | None:
    """One line back into a record, or None if it is not one.

    A log written by a run that was killed can end in half a line, and a log
    written by a later version can carry a record this one has never heard of.
    Neither is a reason to refuse to render the rest.
    """
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    model = TYPES.get(str(raw.get("t", "")))
    if model is None:
        return None
    try:
        return model.model_validate(raw)
    except Exception:  # noqa: BLE001 — a malformed record costs its own line, not the log
        return None


Submission = tuple[str, list[SubmittedFinding], list[SubmittedVerdict]]


def submitted(payload: dict[str, Any] | None) -> Submission:
    """The terminal tool's payload, reduced to what the page shows.

    Read defensively rather than through the pipeline's models: this is raw
    model output, and the log has to survive a payload the pipeline would have
    thrown away.
    """
    if not payload:
        return "", [], []
    summary = str(payload.get("summary", "") or "").strip()
    return summary, _findings(payload.get("findings")), _verdicts(payload.get("verdicts"))


def _findings(raw: Any) -> list[SubmittedFinding]:
    rows: list[SubmittedFinding] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        file = str(entry.get("file", "") or "")
        line = entry.get("line")
        rows.append(
            SubmittedFinding(
                severity=str(entry.get("severity", "") or ""),
                location=f"{file}:{line}" if file and line else file,
                title=str(entry.get("title", "") or "").strip(),
            )
        )
    return rows


def _verdicts(raw: Any) -> list[SubmittedVerdict]:
    rows: list[SubmittedVerdict] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        rows.append(
            SubmittedVerdict(
                finding_id=str(entry.get("finding_id", "") or ""),
                verdict=str(entry.get("verdict", "") or ""),
                reason=str(entry.get("reason", "") or "").strip(),
            )
        )
    return rows
