"""Data models of the review pipeline.

Everything the agents hand back goes through these models — no free-form text is
passed between stages, otherwise merging and the judge become unreliable.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def repo_path(value: str) -> str:
    """A path as the tree names it — no surrounding whitespace, no leading `./`.

    The one spelling every comparison uses: the scope gate looks a finding's
    file up in the change map by it, the merge groups findings by it, and the
    fingerprint and the machine formats print it. `Finding.file` is normalised
    on the way in, so a reader of that field never has to do this again.
    """
    return value.strip().lstrip("./")


# str + Enum rather than StrEnum: templates format a severity into text, and
# the two spell that differently.
class Severity(str, Enum):  # noqa: UP042
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


SEVERITY_ORDER: dict[Severity, int] = {
    Severity.BLOCKER: 0,
    Severity.MAJOR: 1,
    Severity.MINOR: 2,
    Severity.NIT: 3,
}

SEVERITY_LABEL: dict[Severity, str] = {
    Severity.BLOCKER: "Blocker",
    Severity.MAJOR: "Major",
    Severity.MINOR: "Minor",
    Severity.NIT: "Nit",
}


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Part of prompt_tokens served from the provider's prefix cache.
    cached_tokens: int = 0
    # Whether the provider reported that number at all. A gateway may serve the
    # prefix from cache and still leave prompt_tokens_details empty, so zero
    # hits and no statistics are different states.
    cache_reported: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cache_hit_rate(self) -> float:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            cache_reported=self.cache_reported or other.cache_reported,
        )


class Finding(BaseModel):
    """A single finding. `line` is a line number in the new file version."""

    id: str = ""
    file: str
    line: int | None = None
    end_line: int | None = None
    severity: Severity = Severity.MINOR
    category: str = "general"
    title: str
    rationale: str
    suggestion: str | None = None
    confidence: float = 0.5
    # Ids of the checklist items that found it (several after deduplication)
    sources: list[str] = Field(default_factory=list)

    @field_validator("file")
    @classmethod
    def _normalise_file(cls, v: str) -> str:
        return repo_path(v)

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("line", "end_line", mode="before")
    @classmethod
    def _coerce_line(cls, v: object) -> int | None:
        if v in (None, "", 0):
            return None
        try:
            n = int(v)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    @property
    def location(self) -> str:
        """`file:line` — or the file and the word for what is missing. This is
        the one string every surface prints for where a finding is, so a
        finding with no line says so here rather than reading like one about
        the file as a whole."""
        return f"{self.file}:{self.line}" if self.line else f"{self.file} (no line)"


# "truncated" is a result, not a failure: the agent submitted findings, but the
# turn limit ended the review rather than the agent deciding it was done.
ItemStatus = Literal["pending", "running", "ok", "truncated", "failed", "skipped"]


class ItemResult(BaseModel):
    """Result of running a single checklist item."""

    item_id: str
    item_title: str
    status: ItemStatus = "pending"
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    error: str | None = None
    usage: Usage = Field(default_factory=Usage)
    turns: int = 0
    duration_s: float = 0.0


VerdictKind = Literal["confirmed", "false_positive", "nitpick", "duplicate", "unreviewed"]


class Verdict(BaseModel):
    finding_id: str
    verdict: VerdictKind = "unreviewed"
    severity: Severity | None = None
    reason: str = ""


class DiffStat(BaseModel):
    file: str
    status: str
    added: int = 0
    removed: int = 0


class ReviewRun(BaseModel):
    run_id: str
    repo_root: str
    branch: str
    target: str
    base_sha: str
    head_sha: str
    model: str
    started_at: str
    finished_at: str | None = None
    files: list[DiffStat] = Field(default_factory=list)
    items: list[ItemResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    # Findings that pointed away from what the MR changed. Kept out of the report
    # proper and out of the judging, but written down: a dropped finding the
    # author cannot see is indistinguishable from one that was never made.
    out_of_scope: list[Finding] = Field(default_factory=list)
    verdicts: dict[str, Verdict] = Field(default_factory=dict)
    judge_summary: str = ""
    judge_usage: Usage = Field(default_factory=Usage)

    @property
    def total_usage(self) -> Usage:
        acc = self.judge_usage
        for item in self.items:
            acc = acc + item.usage
        return acc

    def confirmed(self) -> list[Finding]:
        keep = {"confirmed", "nitpick", "unreviewed"}
        return [f for f in self.findings if self.verdict_on(f) in keep]

    def rejected(self) -> list[Finding]:
        drop = {"false_positive", "duplicate"}
        return [f for f in self.findings if self.verdict_on(f) in drop]

    def verdict_on(self, finding: Finding) -> VerdictKind:
        """A finding nobody judged counts as unreviewed rather than as missing."""
        return self.verdicts.get(finding.id, Verdict(finding_id=finding.id)).verdict
