"""The view model a report is rendered from.

`ReviewRun` is the pipeline's own record: raw usage counters, verdicts in a
dictionary keyed by finding id, every finding regardless of what the judge said.
Templates should not have to know any of that, and three of them answering the
same question ("was this finding rejected?") in three slightly different ways is
how report formats drift apart.

So the counting and the filtering happen once, here, and produce a flat
structure. What stays out on purpose is wording and markup: severity labels,
icons, the sentence explaining an unreported cache — those differ between
markdown, HTML and a merge request comment, and belong to whichever template is
rendering. The cache, for instance, hands over a state and the numbers, not a
paragraph.

This is a published contract: user templates read these fields, so renaming one
breaks them. Adding a field does not, which is why it stays deliberately small.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum

from pydantic import BaseModel, Field

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


class CacheState(str, Enum):
    """Three outcomes, not two.

    The same ~24k context block is resent on every turn, so a run either costs
    full price or a fraction of it. `UNKNOWN` is not a polite way of saying zero:
    a gateway may serve the shared prefix from its cache and still leave
    `usage.prompt_tokens_details` empty, so silence says nothing either way.
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


class FindingView(BaseModel):
    id: str
    file: str
    line: int | None
    end_line: int | None
    location: str
    severity: Severity
    category: str
    confidence: float
    title: str
    rationale: str
    suggestion: str | None
    sources: list[str]
    verdict: VerdictKind | None
    # Set only when the judge actually said something about this finding: an
    # `unreviewed` verdict carries no judgement and must not be shown as one.
    verdict_reason: str | None


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


class ReviewView(BaseModel):
    meta: RunMeta
    stats: RunStats
    cache: CacheView
    judge_summary: str
    findings: list[FindingView]
    rejected: list[FindingView]
    items: list[ItemView]
    failed_items: list[ItemView]
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
    """Prose comes from the model and its trailing whitespace is an accident.
    Stripping it here keeps templates from having to guard every blank line."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _finding(finding: Finding, run: ReviewRun) -> FindingView:
    verdict = run.verdicts.get(finding.id)
    reason = None
    if verdict is not None and verdict.verdict != "unreviewed":
        reason = _text(verdict.reason)
    return FindingView(
        id=finding.id,
        file=finding.file,
        line=finding.line,
        end_line=finding.end_line,
        location=finding.location,
        severity=finding.severity,
        category=finding.category,
        confidence=finding.confidence,
        title=finding.title.strip(),
        rationale=finding.rationale.strip(),
        suggestion=_text(finding.suggestion),
        sources=list(finding.sources),
        verdict=verdict.verdict if verdict is not None else None,
        verdict_reason=reason,
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
    )


def build_view(run: ReviewRun) -> ReviewView:
    confirmed = [_finding(f, run) for f in run.confirmed()]
    rejected = [_finding(f, run) for f in run.rejected()]
    counts = Counter(f.severity for f in confirmed)
    items = [_item(i) for i in run.items]

    return ReviewView(
        meta=RunMeta(
            run_id=run.run_id,
            repo_root=run.repo_root,
            branch=run.branch,
            target=run.target,
            base_sha=run.base_sha,
            head_sha=run.head_sha,
            model=run.model,
            started_at=run.started_at,
            finished_at=run.finished_at,
        ),
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
        items=items,
        failed_items=[i for i in items if i.status == "failed"],
        files=list(run.files),
    )
