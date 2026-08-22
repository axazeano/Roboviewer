"""The view model a report is rendered from.

`ReviewRun` is the pipeline's own record: raw counters, verdicts keyed by
finding id, every finding regardless of what the judge said. Counting and
filtering happen once here instead, so three templates cannot answer "was this
finding rejected?" three slightly different ways.

Wording and markup stay out. Severity labels, icons and the sentence explaining
an unreported cache belong to whichever template is rendering — the cache hands
over a state and the numbers, not a paragraph.

User templates read these fields, so renaming one breaks them. That is why the
model stays deliberately small.

Two shapes here, and which one a class is says something. `FindingView` extends
`Finding`: a finding means the same thing in a report as it does in the run, and
the report only knows more about it. `ItemView` and `ReviewView` project instead
— they answer questions (`findings_count`, `failed_items`) rather than carry
fields, and their names are chosen for a template rather than for the pipeline.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    SEVERITY_ORDER,
    DiffStat,
    Finding,
    ItemResult,
    ItemStatus,
    ReviewRun,
    Severity,
    Usage,
    VerdictKind,
)


class CacheState(str, Enum):  # noqa: UP042 — see the note on Severity
    """Three outcomes, not two.

    `UNKNOWN` is not a polite way of saying zero: a gateway may serve the shared
    prefix from cache and still leave `usage.prompt_tokens_details` empty, so
    silence says nothing either way. `ZERO` is the only state that means caching
    really did not work.
    """

    HIT = "hit"
    ZERO = "zero"
    UNKNOWN = "unknown"


class CacheView(BaseModel):
    state: CacheState
    prompt_tokens: int
    cached_tokens: int
    hit_rate: float


class RunMeta(BaseModel):
    """Which run this was. Read off `ReviewRun` by name, so the two cannot
    disagree about what a base sha is called."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    repo_root: str
    branch: str
    target: str
    base_sha: str
    head_sha: str
    model: str
    started_at: str
    finished_at: str | None = None


class SeverityCount(BaseModel):
    severity: Severity
    count: int


class RunStats(BaseModel):
    files_changed: int
    added: int
    removed: int
    total_tokens: int
    # Only non-empty severities, worst first — a template iterates it as-is
    by_severity: list[SeverityCount] = Field(default_factory=list)


class FindingView(Finding):
    """The finding plus what only the finished run knows about it.

    Inherited rather than restated: every field a template reads off a finding —
    file, line, severity, title — means the same thing here, and the copy that
    used to declare them again went out of step with `Finding` field by field.
    """

    verdict: VerdictKind | None = None
    # Set only when the judge actually said something about this finding: an
    # `unreviewed` verdict carries no judgement and must not be shown as one.
    verdict_reason: str | None = None
    # Identity across runs, for consumers that track issues between pipelines
    fingerprint: str

    @field_validator("title", "rationale")
    @classmethod
    def _trim(cls, value: str) -> str:
        """Trailing whitespace in model prose is an accident, and every template
        would otherwise have to guard against the blank line it produces."""
        return value.strip()

    @field_validator("suggestion")
    @classmethod
    def _blank_is_nothing(cls, value: str | None) -> str | None:
        return _text(value)


class ItemView(BaseModel):
    id: str
    title: str
    status: ItemStatus
    findings_count: int
    turns: int
    total_tokens: int
    duration_s: float
    cache: CacheView
    error: str | None
    # What the agent concluded. None when it submitted without one, which is
    # itself worth showing — it usually means the agent never got that far.
    summary: str | None


class ReviewView(BaseModel):
    meta: RunMeta
    stats: RunStats
    cache: CacheView
    judge_summary: str
    findings: list[FindingView]
    rejected: list[FindingView]
    # Pointed away from what the MR changed, so never judged. Shown apart from
    # the findings and left out of every count: they may well be true, and they
    # are still not this review's business.
    out_of_scope: list[FindingView]
    items: list[ItemView]
    failed_items: list[ItemView]
    # Items the turn limit cut off. Their findings are in `findings`, but the
    # aspects they cover were not reviewed to the end.
    truncated_items: list[ItemView]
    files: list[DiffStat]


def _cache(usage: Usage) -> CacheView:
    if usage.cached_tokens:
        state = CacheState.HIT
    elif usage.cache_reported:
        state = CacheState.ZERO
    else:
        state = CacheState.UNKNOWN
    return CacheView(
        state=state,
        prompt_tokens=usage.prompt_tokens,
        cached_tokens=usage.cached_tokens,
        hit_rate=usage.cache_hit_rate,
    )


def _text(value: str | None) -> str | None:
    """Trailing whitespace in model prose is an accident. Stripping it here
    saves every template from guarding against a blank line."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _fingerprints(findings: list[Finding]) -> dict[str, str]:
    """File, category and title — deliberately not the line, which an edit above
    would shift, turning every old finding into a new one. Titles come from the
    model, so this is only as stable as the model's wording.

    Collisions get a suffix, because GitLab collapses equal fingerprints and a
    lost finding costs more than a shifted identity.
    """
    result: dict[str, str] = {}
    seen: Counter[str] = Counter()
    for finding in findings:
        material = " ".join(
            [
                finding.file,
                finding.category.strip().casefold(),
                " ".join(finding.title.split()).casefold(),
            ]
        )
        base = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
        seen[base] += 1
        result[finding.id] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    return result


def _finding(finding: Finding, run: ReviewRun, fingerprint: str) -> FindingView:
    verdict = run.verdicts.get(finding.id)
    reason = None
    if verdict is not None and verdict.verdict != "unreviewed":
        reason = _text(verdict.reason)
    return FindingView(
        **finding.model_dump(),
        verdict=verdict.verdict if verdict is not None else None,
        verdict_reason=reason,
        fingerprint=fingerprint,
    )


def _item(item: ItemResult) -> ItemView:
    return ItemView(
        id=item.item_id,
        title=item.item_title,
        status=item.status,
        findings_count=len(item.findings),
        turns=item.turns,
        total_tokens=item.usage.total_tokens,
        duration_s=item.duration_s,
        cache=_cache(item.usage),
        error=item.error,
        summary=_text(item.summary),
    )


def build_view(run: ReviewRun) -> ReviewView:
    # Over all findings at once: uniqueness has to hold across the report
    prints = _fingerprints(run.findings + run.out_of_scope)
    confirmed = [_finding(f, run, prints[f.id]) for f in run.confirmed()]
    rejected = [_finding(f, run, prints[f.id]) for f in run.rejected()]
    out_of_scope = [_finding(f, run, prints[f.id]) for f in run.out_of_scope]
    counts = Counter(f.severity for f in confirmed)
    items = [_item(i) for i in run.items]

    return ReviewView(
        meta=RunMeta.model_validate(run),
        stats=RunStats(
            files_changed=len(run.files),
            added=sum(f.added for f in run.files),
            removed=sum(f.removed for f in run.files),
            total_tokens=run.total_usage.total_tokens,
            by_severity=[
                SeverityCount(severity=s, count=counts[s])
                for s in sorted(counts, key=lambda s: SEVERITY_ORDER[s])
            ],
        ),
        cache=_cache(run.total_usage),
        judge_summary=run.judge_summary.strip(),
        findings=confirmed,
        rejected=rejected,
        out_of_scope=out_of_scope,
        items=items,
        failed_items=[i for i in items if i.status == "failed"],
        truncated_items=[i for i in items if i.status == "truncated"],
        files=list(run.files),
    )
