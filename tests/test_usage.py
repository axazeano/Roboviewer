"""Token accounting and prefix-cache hits.

Zero hits and no statistics are different states: a provider may serve the
shared prefix from cache without counting it. The report has to tell them
apart, so all three outcomes are checked.
"""

from __future__ import annotations

from types import SimpleNamespace

from roboviewer.models import ItemResult, ReviewRun, Usage
from roboviewer.report import render_report
from roboviewer.runners.openai_agent import _extract_usage


def usage_from(raw: dict) -> Usage:
    return _extract_usage(SimpleNamespace(usage=raw))


BASE = {"prompt_tokens": 100, "completion_tokens": 5}


# ---------------------------------------------------------------- reading usage


def test_empty_details_means_not_reported_rather_than_zero_hits() -> None:
    # This is how bare vLLM answers: serves the prefix from cache, reports nothing
    usage = usage_from({**BASE, "prompt_tokens_details": None})
    assert usage.cached_tokens == 0
    assert not usage.cache_reported


def test_explicit_zero_is_reported() -> None:
    usage = usage_from({**BASE, "prompt_tokens_details": {"cached_tokens": 0}})
    assert usage.cached_tokens == 0
    assert usage.cache_reported


def test_hits_are_read_from_details_and_from_aliases() -> None:
    assert usage_from({**BASE, "prompt_tokens_details": {"cached_tokens": 64}}).cached_tokens == 64
    assert usage_from({**BASE, "prompt_cache_hit_tokens": 70}).cached_tokens == 70
    assert usage_from({**BASE, "cache_read_input_tokens": 80}).cached_tokens == 80


def test_missing_usage_block_does_not_crash() -> None:
    assert _extract_usage(SimpleNamespace(usage=None)) == Usage()


def test_sum_keeps_the_fact_that_someone_reported() -> None:
    silent = Usage(prompt_tokens=10)
    talking = Usage(prompt_tokens=10, cached_tokens=4, cache_reported=True)
    assert (silent + talking).cache_reported
    assert (silent + silent).cache_reported is False


# --------------------------------------------------------------- in the report


def run_with(usage: Usage) -> ReviewRun:
    return ReviewRun(
        run_id="20260802-120000",
        repo_root=".",
        branch="feature/x",
        target="develop",
        base_sha="a" * 40,
        head_sha="b" * 40,
        model="test-model",
        started_at="",
        items=[ItemResult(item_id="correctness", item_title="Корректность",
                          status="ok", usage=usage, turns=3)],
    )


def test_silence_is_not_reported_as_a_failure_to_cache() -> None:
    text = render_report(run_with(Usage(prompt_tokens=100, completion_tokens=5)))
    assert "From cache: unknown" in text
    assert "reports no statistics" in text
    assert "| n/a |" in text, "the item table must not show a confident zero either"


def test_reported_zero_says_the_cache_really_did_not_fire() -> None:
    usage = Usage(prompt_tokens=100, completion_tokens=5, cache_reported=True)
    text = render_report(run_with(usage))
    assert "From cache: 0" in text
    assert "the prefix differs every time" in text


def test_hits_are_shown_with_their_share() -> None:
    usage = Usage(prompt_tokens=100, completion_tokens=5, cached_tokens=64, cache_reported=True)
    text = render_report(run_with(usage))
    assert "From cache: 64 prompt tokens (64% of input)" in text
