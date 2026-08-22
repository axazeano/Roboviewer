"""Pacing a run against what the provider will take.

The clock and the sleep are injected, so a minute of pacing costs a microsecond
and nothing here waits for real. What is worth checking is not that a limiter
exists but that it holds the right things back: that a burst is spread out
rather than sent, that an estimate is corrected by what the response reported,
and that one agent's 429 stops the other three too — which is the difference
between a slow run and a failed one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, RateLimitError

from roboviewer.config import ProviderConfig, RateLimits, RunConfig
from roboviewer.provider import openai_agent
from roboviewer.provider.openai_agent import OpenAIAgentRunner
from roboviewer.provider.ratelimit import (
    PROMPT,
    UNCACHED,
    RateLimiter,
    estimate_tokens,
    retry_after,
)


class Fake:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limiter(fake: Fake, **limits: object) -> RateLimiter:
    return RateLimiter(RateLimits(**limits), clock=fake.clock, sleep=fake.sleep)


# ------------------------------------------------------------------ spreading a burst out


def test_requests_that_fit_the_minute_are_not_held() -> None:
    fake = Fake()
    limit = limiter(fake, prompt_tokens_per_minute=1000)

    for _ in range(4):
        reservation = asyncio.run(limit.reserve(prompt=200, generated=0))
        assert not reservation.held

    assert fake.slept == []


def test_the_one_that_would_go_over_waits_for_the_window_to_roll() -> None:
    fake = Fake()
    limit = limiter(fake, prompt_tokens_per_minute=1000)
    asyncio.run(limit.reserve(prompt=800, generated=0))

    fake.now += 10.0  # the first request is ten seconds old
    reservation = asyncio.run(limit.reserve(prompt=800, generated=0))

    # It waits out the remaining fifty seconds of the first one, not a fixed guess
    assert reservation.waited == pytest.approx(50.0)
    assert reservation.reason == PROMPT


def test_the_generated_ceiling_holds_a_request_back_on_its_own() -> None:
    fake = Fake()
    limit = limiter(fake, generated_tokens_per_minute=8000)
    asyncio.run(limit.reserve(prompt=0, generated=8000))

    reservation = asyncio.run(limit.reserve(prompt=0, generated=8000))

    assert reservation.waited == pytest.approx(60.0)


def test_a_request_larger_than_the_whole_minute_is_sent_rather_than_hung() -> None:
    # Waiting cannot make it fit, and hanging forever is the worst answer of the
    # three. Let the provider be the one to refuse it.
    fake = Fake()
    limit = limiter(fake, prompt_tokens_per_minute=1000)

    reservation = asyncio.run(limit.reserve(prompt=5000, generated=0))

    assert not reservation.held


def test_nothing_configured_paces_nothing() -> None:
    fake = Fake()
    limit = limiter(fake)

    for _ in range(50):
        assert not asyncio.run(limit.reserve(prompt=1_000_000, generated=8000)).held


# ------------------------------------------------------------------ the estimate is corrected


def test_the_reported_count_replaces_the_estimate() -> None:
    fake = Fake()
    limit = limiter(fake, prompt_tokens_per_minute=1000)

    first = asyncio.run(limit.reserve(prompt=900, generated=0))
    # The estimate was four times the truth; without settling, the next request
    # would wait a minute for room that is already there
    limit.settle(first, prompt=200, uncached=200, generated=50)

    assert not asyncio.run(limit.reserve(prompt=700, generated=0)).held


def test_the_uncached_budget_is_charged_only_what_the_cache_missed() -> None:
    fake = Fake()
    limit = limiter(fake, uncached_prompt_tokens_per_minute=1000)

    first = asyncio.run(limit.reserve(prompt=900, generated=0))
    # A shared prefix: nearly all of it came from the provider's cache
    limit.settle(first, prompt=900, uncached=100, generated=50)

    # Which is the whole point of the prefix — the tight budget is barely touched
    assert not asyncio.run(limit.reserve(prompt=800, generated=0)).held


def test_before_the_answer_the_uncached_budget_assumes_the_worst() -> None:
    fake = Fake()
    limit = limiter(fake, uncached_prompt_tokens_per_minute=1000)
    asyncio.run(limit.reserve(prompt=900, generated=0))

    reservation = asyncio.run(limit.reserve(prompt=900, generated=0))

    assert reservation.reason == UNCACHED
    assert reservation.held


# ------------------------------------------------------------------ when the provider says no


def test_a_429_holds_every_agent_not_only_the_one_refused() -> None:
    fake = Fake()
    limit = limiter(fake)  # nothing configured: the cooldown still applies

    limit.pause(30.0)
    reservation = asyncio.run(limit.reserve(prompt=10, generated=10))

    assert reservation.waited == pytest.approx(30.0)
    assert "429" in reservation.reason


def test_a_second_refusal_extends_the_hold_rather_than_shortening_it() -> None:
    fake = Fake()
    limit = limiter(fake)

    limit.pause(30.0)
    still_held = limit.pause(5.0)

    # Three agents refused in the same second must not each undo the first hold
    assert still_held == pytest.approx(30.0)


def test_the_hold_ends_by_itself() -> None:
    fake = Fake()
    limit = limiter(fake)
    limit.pause(30.0)

    fake.now += 31.0

    assert not asyncio.run(limit.reserve(prompt=10, generated=10)).held


# ------------------------------------------------------------------ what the provider advertises


def test_the_advertised_ceilings_are_adopted() -> None:
    fake = Fake()
    limit = limiter(fake)
    headers = {
        "x-ratelimit-limit-tokens-prompt": "21600000",
        "x-ratelimit-limit-tokens-cache-adjusted-prompt": "5400000",
        "x-ratelimit-limit-tokens-generated": "216000",
    }

    adopted = limit.observe(headers)

    assert adopted == {PROMPT: 21_600_000, UNCACHED: 5_400_000, "generated tokens": 216_000}
    # And they are in force: a request over the advertised uncached ceiling waits
    asyncio.run(limit.reserve(prompt=5_000_000, generated=0))
    assert asyncio.run(limit.reserve(prompt=1_000_000, generated=0)).held


def test_adopting_can_be_switched_off() -> None:
    fake = Fake()
    limit = limiter(fake, adopt_advertised=False)

    assert limit.observe({"x-ratelimit-limit-tokens-prompt": "21600000"}) == {}


def test_headers_that_say_nothing_useful_change_nothing() -> None:
    fake = Fake()
    limit = limiter(fake, prompt_tokens_per_minute=1000)

    assert limit.observe(None) == {}
    assert limit.observe({}) == {}
    assert limit.observe({"x-ratelimit-limit-tokens-prompt": "not a number"}) == {}
    assert limit.observe({"X-RateLimit-Limit-Tokens-Prompt": "1000"}) == {}  # already that


def test_the_same_ceiling_twice_is_not_reported_as_a_change() -> None:
    fake = Fake()
    limit = limiter(fake)
    headers = {"x-ratelimit-limit-tokens-generated": "216000"}

    assert limit.observe(headers)
    assert limit.observe(headers) == {}


# ------------------------------------------------------------------ reading the request


def test_the_estimate_grows_with_the_request() -> None:
    small = estimate_tokens([{"role": "user", "content": "hello"}])
    large = estimate_tokens([{"role": "user", "content": "hello" * 1000}])

    assert small > 0
    assert large > small * 100


def test_an_unserialisable_request_estimates_zero_rather_than_raising() -> None:
    assert estimate_tokens(object()) > 0  # default=str keeps it working
    assert estimate_tokens({"f": lambda: None}) > 0


def test_retry_after_is_read_when_the_provider_sends_one() -> None:
    class Refused(Exception):
        response = type("R", (), {"headers": {"retry-after": "42"}})()

    assert retry_after(Refused()) == pytest.approx(42.0)


def test_a_refusal_without_a_retry_after_says_nothing() -> None:
    class Bare(Exception):
        pass

    assert retry_after(Bare()) is None

    class Dated(Exception):
        response = type("R", (), {"headers": {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}})()

    # An HTTP-date is better answered by the caller's backoff than by a guess
    assert retry_after(Dated()) is None


# ------------------------------------------------------------------ the config knobs


def test_the_limits_default_to_off_so_nothing_changes_unasked() -> None:
    limits = ProviderConfig().rate_limits

    assert limits.prompt_tokens_per_minute == 0
    assert limits.uncached_prompt_tokens_per_minute == 0
    assert limits.generated_tokens_per_minute == 0
    assert limits.requests_per_minute == 0
    # Except this one: the provider knows its own ceilings and says so
    assert limits.adopt_advertised


def test_a_typo_in_the_limits_section_is_refused() -> None:
    with pytest.raises(ValueError, match="promt_tokens_per_minute"):
        ProviderConfig.model_validate({"rate_limits": {"promt_tokens_per_minute": 10}})


# ------------------------------------------------------------------ the runner asks first


def completion(prompt: int = 1000, cached: int = 0, generated: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": generated,
            "prompt_tokens_details": {"cached_tokens": cached},
        }
    )


def refusal(status: int = 429, headers: dict[str, str] | None = None) -> APIStatusError:
    response = httpx.Response(
        status,
        headers=headers or {},
        request=httpx.Request("POST", "https://api.example/v1/chat/completions"),
    )
    error = RateLimitError if status == 429 else APIStatusError
    return error("refused", response=response, body=None)


class StubClient:
    """The provider, scripted: each answer is either a completion or a refusal."""

    def __init__(self, *answers: object, headers: dict[str, str] | None = None) -> None:
        self.answers = list(answers)
        self.headers = headers or {}
        self.sent = 0
        create = self._create
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )

    async def _create(self, **kwargs: object) -> object:  # noqa: ARG002 — the SDK signature
        self.sent += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return self.wrapper(answer, self.headers)

    @staticmethod
    def wrapper(completion: object, headers: dict[str, str]) -> object:
        """What `with_raw_response` hands back: the legacy wrapper, whose
        `parse()` is an ordinary method rather than a coroutine."""
        return SimpleNamespace(headers=headers, parse=lambda: completion)


class NewStyleClient(StubClient):
    """The other wrapper the SDK has — `AsyncAPIResponse`, where `parse()` is a
    coroutine. Both shapes exist in the wild; the runner must not care."""

    @staticmethod
    def wrapper(completion: object, headers: dict[str, str]) -> object:
        async def parse() -> object:
            return completion

        return SimpleNamespace(headers=headers, parse=parse)


def runner_with(
    client: StubClient, tmp_path: Path, **limits: object
) -> tuple[OpenAIAgentRunner, Fake]:
    """A runner talking to the stub, on a clock that only moves when it waits."""
    provider = ProviderConfig(api_key="k", rate_limits=RateLimits(**limits), max_retries=2)
    runner = OpenAIAgentRunner(provider, RunConfig(), tmp_path, "base", "head")
    fake = Fake()
    runner._client = client  # type: ignore[assignment]
    runner._limiter = RateLimiter(provider.rate_limits, clock=fake.clock, sleep=fake.sleep)
    return runner, fake


def send(
    runner: OpenAIAgentRunner,
    events: list[tuple[str, str]] | None = None,
    prompt_tokens: int = 2000,
) -> object:
    """One request of roughly the asked-for size, since the estimate is what the
    limiter sees before the provider answers."""
    body = {
        "messages": [{"role": "user", "content": "x" * (prompt_tokens * 4)}],
        "max_tokens": 8000,
        "tools": [],
    }
    hook = (lambda kind, detail: events.append((kind, detail))) if events is not None else _quiet
    return asyncio.run(runner._send(body, hook))


def _quiet(kind: str, detail: str) -> None:
    pass


def test_the_reported_usage_settles_what_the_request_reserved(tmp_path: Path) -> None:
    # Two requests estimated at 2000 uncached tokens each do not both fit in
    # 3500. The answer says only 1000 of the first missed the cache, and that is
    # what has to be charged — otherwise the prefix cache buys nothing here.
    client = StubClient(completion(prompt=5000, cached=4000, generated=100))
    runner, fake = runner_with(client, tmp_path, uncached_prompt_tokens_per_minute=3500)

    send(runner)
    send(runner)

    assert client.sent == 2
    assert fake.slept == []


def test_without_settling_the_second_request_would_have_waited(tmp_path: Path) -> None:
    # The same two requests, with the provider reporting no cache hits at all
    client = StubClient(completion(prompt=5000, cached=0, generated=100))
    runner, fake = runner_with(client, tmp_path, uncached_prompt_tokens_per_minute=3500)

    send(runner)
    send(runner)

    assert fake.slept  # held back rather than refused
    assert client.sent == 2


def test_a_429_is_retried_and_holds_everyone_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(openai_agent, "RETRY_DELAY_S", 0.0)
    client = StubClient(refusal(headers={"retry-after": "30"}), completion())
    runner, fake = runner_with(client, tmp_path)
    events: list[tuple[str, str]] = []

    send(runner, events)

    assert client.sent == 2
    assert tuple(kind for kind, _ in events if kind == "retry") == ("retry", )
    assert "holding every agent 30s" in events[0][1]
    # The retry itself waited out the hold rather than asking straight away
    assert fake.slept == [pytest.approx(30.0)]


def test_the_ceilings_the_provider_advertises_are_announced_once(tmp_path: Path) -> None:
    client = StubClient(completion(), headers={"x-ratelimit-limit-tokens-generated": "216000"})
    runner, _ = runner_with(client, tmp_path)
    events: list[tuple[str, str]] = []

    send(runner, events)
    send(runner, events)

    # Adopted on the first answer; the second says the same thing and is silent
    assert [kind for kind, _ in events] == ["limits"]
    assert "216000/min" in events[0][1]


def test_a_wait_is_announced_rather_than_looking_like_a_hang(tmp_path: Path) -> None:
    client = StubClient(completion(prompt=2000, generated=100))
    runner, fake = runner_with(client, tmp_path, prompt_tokens_per_minute=3000)
    events: list[tuple[str, str]] = []

    send(runner, events)
    send(runner, events)

    assert [kind for kind, _ in events] == ["paced"]
    assert "prompt tokens" in events[0][1]
    assert fake.slept


def test_both_raw_response_shapes_are_handled(tmp_path: Path) -> None:
    # The SDK has two: `with_raw_response` gives the legacy wrapper with a plain
    # parse(), the newer AsyncAPIResponse.parse is a coroutine. Awaiting the
    # wrong one fails on the first turn of every agent, which is how it was found.
    for client in (StubClient(completion()), NewStyleClient(completion())):
        runner, _ = runner_with(client, tmp_path)

        assert send(runner) is not None
        assert client.sent == 1


def test_a_bad_request_is_not_retried(tmp_path: Path) -> None:
    client = StubClient(refusal(400))
    runner, _ = runner_with(client, tmp_path)

    with pytest.raises(APIStatusError):
        send(runner)

    assert client.sent == 1
