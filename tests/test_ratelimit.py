"""Pacing a run against what the gateway will take.

The clock and the sleep are injected, so a minute of pacing costs a microsecond
and nothing here waits for real. What is worth checking is not that a limiter
exists but that it holds the right things back: that a burst is spread out
rather than sent, that an estimate is corrected by what the response reported,
and that one agent's 429 stops the other three too — which is the difference
between a slow run and a failed one.

The four gateway families each get their own section, because the thing they
disagree about is not a number: one keeps its own count, one is told what is
left, one refuses to charge for a ceiling, and one meters nothing at all.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, RateLimitError

from roboviewer import dialects
from roboviewer.config import ProviderConfig, RateLimits, RunConfig
from roboviewer.dialects import (
    ANTHROPIC,
    FIREWORKS,
    GENERATED,
    INPUT,
    NONE,
    OPENAI,
    OUTPUT,
    PROMPT,
    TOKENS,
    UNCACHED,
    Shape,
    Spent,
    estimate_tokens,
    retry_after,
)
from roboviewer.ratelimit import RateLimiter
from roboviewer.runners import openai_agent
from roboviewer.runners.openai_agent import OpenAIAgentRunner


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


def limiter(
    fake: Fake,
    ceilings: dict[str, int] | None = None,
    *,
    dialect: dialects.Dialect = FIREWORKS,
    adopt: bool = True,
) -> RateLimiter:
    return RateLimiter(
        dialect, ceilings or {}, adopt_advertised=adopt, clock=fake.clock, sleep=fake.sleep
    )


def book(limit: RateLimiter, prompt: int = 0, output: int = 0) -> object:
    return asyncio.run(limit.reserve(Shape(prompt=prompt, output_ceiling=output)))


# ------------------------------------------------------------------ spreading a burst out


def test_requests_that_fit_the_minute_are_not_held() -> None:
    fake = Fake()
    limit = limiter(fake, {PROMPT: 1000})

    for _ in range(4):
        assert not book(limit, prompt=200).held

    assert fake.slept == []


def test_the_one_that_would_go_over_waits_for_the_window_to_roll() -> None:
    fake = Fake()
    limit = limiter(fake, {PROMPT: 1000})
    book(limit, prompt=800)

    fake.now += 10.0  # the first request is ten seconds old
    reservation = book(limit, prompt=800)

    # It waits out the remaining fifty seconds of the first one, not a fixed guess
    assert reservation.waited == pytest.approx(50.0)
    assert reservation.reason == PROMPT


def test_the_generated_ceiling_holds_a_request_back_on_its_own() -> None:
    fake = Fake()
    limit = limiter(fake, {GENERATED: 8000})
    book(limit, output=8000)

    assert book(limit, output=8000).waited == pytest.approx(60.0)


def test_a_request_larger_than_the_whole_minute_is_sent_rather_than_hung() -> None:
    # Waiting cannot make it fit, and hanging forever is the worst answer of the
    # three. Let the gateway be the one to refuse it.
    fake = Fake()
    limit = limiter(fake, {PROMPT: 1000})

    assert not book(limit, prompt=5000).held


def test_nothing_configured_paces_nothing() -> None:
    fake = Fake()
    limit = limiter(fake)

    for _ in range(50):
        assert not book(limit, prompt=1_000_000, output=8000).held


# ------------------------------------------------------------------ the estimate is corrected


def test_the_reported_count_replaces_the_estimate() -> None:
    fake = Fake()
    limit = limiter(fake, {PROMPT: 1000})

    first = book(limit, prompt=900)
    # The estimate was four times the truth; without settling, the next request
    # would wait a minute for room that is already there
    limit.settle(first, Spent(prompt=200, uncached=200, generated=50))

    assert not book(limit, prompt=700).held


def test_the_uncached_budget_is_charged_only_what_the_cache_missed() -> None:
    fake = Fake()
    limit = limiter(fake, {UNCACHED: 1000})

    first = book(limit, prompt=900)
    # A shared prefix: nearly all of it came from the gateway's cache
    limit.settle(first, Spent(prompt=900, uncached=100, generated=50))

    # Which is the whole point of the prefix — the tight budget is barely touched
    assert not book(limit, prompt=800).held


def test_before_the_answer_the_uncached_budget_assumes_the_worst() -> None:
    fake = Fake()
    limit = limiter(fake, {UNCACHED: 1000})
    book(limit, prompt=900)

    reservation = book(limit, prompt=900)

    assert reservation.reason == UNCACHED
    assert reservation.held


# ------------------------------------------------------------------ when the gateway says no


def test_a_429_holds_every_agent_not_only_the_one_refused() -> None:
    fake = Fake()
    limit = limiter(fake)  # nothing configured: the cooldown still applies

    limit.pause(30.0)
    reservation = book(limit, prompt=10, output=10)

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

    assert not book(limit, prompt=10, output=10).held


# ------------------------------------------ fireworks: ceilings advertised, remainder never


def test_the_advertised_ceilings_are_adopted() -> None:
    fake = Fake()
    limit = limiter(fake)
    headers = {
        "x-ratelimit-limit-tokens-prompt": "21600000",
        "x-ratelimit-limit-tokens-cache-adjusted-prompt": "5400000",
        "x-ratelimit-limit-tokens-generated": "216000",
    }

    adopted = limit.observe(headers)

    assert adopted == {PROMPT: 21_600_000, UNCACHED: 5_400_000, GENERATED: 216_000}
    # And they are in force: a request over the advertised uncached ceiling waits
    book(limit, prompt=5_000_000)
    assert book(limit, prompt=1_000_000).held


def test_adopting_can_be_switched_off() -> None:
    fake = Fake()
    limit = limiter(fake, adopt=False)

    assert limit.observe({"x-ratelimit-limit-tokens-prompt": "21600000"}) == {}


def test_headers_that_say_nothing_useful_change_nothing() -> None:
    fake = Fake()
    limit = limiter(fake, {PROMPT: 1000})

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


def test_a_ceiling_that_shrinks_mid_run_shrinks_the_budget() -> None:
    # Adaptive plans move in both directions, and the run has to follow down
    fake = Fake()
    limit = limiter(fake, {GENERATED: 216_000})

    limit.observe({"x-ratelimit-limit-tokens-generated": "12000"})

    assert book(limit, output=8000).waited == 0.0
    assert book(limit, output=8000).held  # 16000 no longer fits under 12000


# ---------------------------------------------- openai: what is left is reported, so believe it


def test_the_reported_remainder_overrules_the_local_count() -> None:
    fake = Fake()
    limit = limiter(fake, {TOKENS: 1000}, dialect=OPENAI)
    book(limit, prompt=900)  # the run believes it has 100 left

    # The gateway says otherwise, and it is the one that also sees every other
    # process spending the same key
    limit.observe({"x-ratelimit-limit-tokens": "1000", "x-ratelimit-remaining-tokens": "950"})

    assert not book(limit, prompt=900).held


def test_a_remainder_of_nothing_holds_the_run_until_the_reported_reset() -> None:
    fake = Fake()
    limit = limiter(fake, {TOKENS: 1000}, dialect=OPENAI)

    limit.observe(
        {
            "x-ratelimit-limit-tokens": "1000",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "20s",
        }
    )

    # Twenty seconds because the gateway said twenty, not sixty because a window
    # happens to be a minute long
    assert book(limit, prompt=500).waited == pytest.approx(20.0)


def test_a_remainder_without_a_ceiling_to_subtract_it_from_is_ignored() -> None:
    fake = Fake()
    limit = limiter(fake, dialect=OPENAI)  # nothing configured, nothing advertised

    limit.observe({"x-ratelimit-remaining-tokens": "0"})

    assert not book(limit, prompt=500).held


def test_the_openai_family_charges_input_and_output_to_one_bucket() -> None:
    fake = Fake()
    limit = limiter(fake, {TOKENS: 1000}, dialect=OPENAI)

    # 600 prompt + 500 ceiling is over the one combined budget
    assert book(limit, prompt=600, output=500).held is False  # larger than the window: sent
    limit_two = limiter(Fake(), {TOKENS: 5000}, dialect=OPENAI)
    book(limit_two, prompt=3000, output=2000)
    assert book(limit_two, prompt=100).held


# ------------------------------------------ anthropic: cache reads free, the ceiling not charged


def test_the_output_ceiling_is_not_booked_where_it_is_not_charged() -> None:
    fake = Fake()
    limit = limiter(fake, {OUTPUT: 8000}, dialect=ANTHROPIC)

    # Four turns each carrying max_tokens=8000 would have exhausted the budget
    # on the first one under a dialect that books the ceiling
    for _ in range(4):
        assert not book(limit, output=8000).held


def test_what_was_actually_generated_is_charged_after_the_fact() -> None:
    # Booking nothing means the budget cannot hold anything back in advance; it
    # bites once real generation has overspent it. Before that point the
    # remainder this gateway reports on every answer is what paces the run.
    fake = Fake()
    limit = limiter(fake, {OUTPUT: 1000}, dialect=ANTHROPIC)

    for produced in (900, 400):
        limit.settle(book(limit, output=8000), Spent(prompt=0, uncached=0, generated=produced))

    assert book(limit, output=8000).held
    assert fake.slept


def test_the_reported_output_remainder_is_what_actually_paces_it() -> None:
    fake = Fake()
    limit = limiter(fake, {OUTPUT: 1000}, dialect=ANTHROPIC)

    limit.observe(
        {
            "anthropic-ratelimit-output-tokens-limit": "1000",
            "anthropic-ratelimit-output-tokens-remaining": "0",
            "anthropic-ratelimit-output-tokens-reset": "15s",
        }
    )

    assert book(limit, output=8000).waited == pytest.approx(15.0)


def test_cache_reads_do_not_count_against_the_input_budget() -> None:
    fake = Fake()
    limit = limiter(fake, {INPUT: 1000}, dialect=ANTHROPIC)

    first = book(limit, prompt=900)
    limit.settle(first, Spent(prompt=900, uncached=100, generated=50))

    assert not book(limit, prompt=800).held


def test_an_anthropic_reset_is_a_timestamp_rather_than_a_duration() -> None:
    fake = Fake()
    limit = limiter(fake, {INPUT: 1000}, dialect=ANTHROPIC)

    limit.observe(
        {
            "anthropic-ratelimit-input-tokens-limit": "1000",
            "anthropic-ratelimit-input-tokens-remaining": "0",
            # Long past, so the wait it implies is zero rather than unparsed
            "anthropic-ratelimit-input-tokens-reset": "2020-01-01T00:00:00Z",
        }
    )

    assert book(limit, prompt=500).waited == pytest.approx(0.0)


# ------------------------------------------------------ none: metered by nothing but concurrency


def test_a_gateway_that_meters_nothing_holds_nothing_back() -> None:
    fake = Fake()
    limit = limiter(fake, dialect=NONE)

    for _ in range(100):
        assert not book(limit, prompt=10_000_000, output=8000).held

    assert fake.slept == []


def test_a_gateway_that_meters_nothing_still_honours_a_refusal() -> None:
    # DeepSeek limits concurrency and answers 429 when it is exceeded; there is
    # no budget to pace against, but a refusal still means everyone waits
    fake = Fake()
    limit = limiter(fake, dialect=NONE)

    limit.pause(30.0)

    assert book(limit, prompt=10).waited == pytest.approx(30.0)


def test_nothing_can_be_configured_for_a_gateway_that_meters_nothing() -> None:
    with pytest.raises(ValueError, match="meters nothing per key"):
        ProviderConfig.model_validate(
            {
                "base_url": "https://api.deepseek.com/v1",
                "rate_limits": {"per_minute": {"tokens": 1}},
            }
        )


# ------------------------------------------------------------------ choosing the family


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.fireworks.ai/inference/v1", "fireworks"),
        ("https://api.anthropic.com/v1", "anthropic"),
        ("https://api.deepseek.com/v1", "none"),
        ("http://localhost:8000/v1", "none"),
        ("https://api.openai.com/v1", "openai"),
        ("https://api.groq.com/openai/v1", "openai"),
        ("https://some.internal.gateway/v1", "openai"),
    ],
)
def test_the_family_is_read_off_the_base_url(base_url: str, expected: str) -> None:
    dialect, why = dialects.resolve("auto", base_url)

    assert dialect.name == expected
    assert why


def test_naming_a_family_beats_guessing_at_it() -> None:
    # A proxy in front of Fireworks hides the host, so the guess would be wrong
    dialect, why = dialects.resolve("fireworks", "https://gateway.internal/v1")

    assert dialect.name == "fireworks"
    assert why == "named in the config"


# ------------------------------------------------------------------ reading the request


def test_the_estimate_grows_with_the_request() -> None:
    small = estimate_tokens([{"role": "user", "content": "hello"}])
    large = estimate_tokens([{"role": "user", "content": "hello" * 1000}])

    assert small > 0
    assert large > small * 100


def test_an_unserialisable_request_estimates_zero_rather_than_raising() -> None:
    assert estimate_tokens(object()) > 0  # default=str keeps it working
    assert estimate_tokens({"f": lambda: None}) > 0


def test_retry_after_is_read_when_the_gateway_sends_one() -> None:
    class Refused(Exception):
        response = type("R", (), {"headers": {"retry-after": "42"}})()

    assert retry_after(Refused()) == pytest.approx(42.0)


def test_a_refusal_without_a_retry_after_says_nothing() -> None:
    class Bare(Exception):
        pass

    assert retry_after(Bare()) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("30", 30.0),
        ("1.5", 1.5),
        ("30s", 30.0),
        ("6m0s", 360.0),
        ("1h2m3s", 3723.0),
        ("500ms", 0.5),
    ],
)
def test_the_three_spellings_of_a_delay_are_all_understood(raw: str, expected: float) -> None:
    class Refused(Exception):
        response = type("R", (), {"headers": {"retry-after": raw}})()

    assert retry_after(Refused()) == pytest.approx(expected)


def test_a_delay_in_no_known_spelling_says_nothing_rather_than_guessing() -> None:
    class Refused(Exception):
        response = type("R", (), {"headers": {"retry-after": "soonish"}})()

    assert retry_after(Refused()) is None


# ------------------------------------------------------------------ the config knobs


def test_the_limits_default_to_off_so_nothing_changes_unasked() -> None:
    limits = ProviderConfig().rate_limits

    assert limits.per_minute == {}
    assert limits.dialect == "auto"
    # Except this one: the gateway knows its own ceilings and says so
    assert limits.adopt_advertised


def test_a_bucket_the_gateway_does_not_meter_is_refused_with_the_ones_it_does() -> None:
    with pytest.raises(ValueError) as raised:
        ProviderConfig.model_validate(
            {
                "base_url": "https://api.fireworks.ai/inference/v1",
                "rate_limits": {"per_minute": {"tokens": 1000}},
            }
        )

    assert "tokens is not metered" in str(raised.value)
    assert "uncached prompt tokens" in str(raised.value)  # and here are the ones that are


def test_a_typo_in_the_limits_section_is_refused() -> None:
    with pytest.raises(ValueError, match="adopt_advertized"):
        ProviderConfig.model_validate({"rate_limits": {"adopt_advertized": True}})


def test_the_old_fixed_buckets_say_where_they_went() -> None:
    from roboviewer.config import MOVED

    assert "provider.rate_limits.prompt_tokens_per_minute" in MOVED
    assert "per_minute" in MOVED["provider.rate_limits.prompt_tokens_per_minute"]


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
    """The gateway, scripted: each answer is either a completion or a refusal."""

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
    client: StubClient,
    tmp_path: Path,
    ceilings: dict[str, int] | None = None,
    dialect: str = "fireworks",
) -> tuple[OpenAIAgentRunner, Fake]:
    """A runner talking to the stub, on a clock that only moves when it waits."""
    provider = ProviderConfig(
        api_key="k",
        max_retries=2,
        rate_limits=RateLimits(dialect=dialect, per_minute=ceilings or {}),
    )
    runner = OpenAIAgentRunner(provider, RunConfig(), tmp_path, "base", "head")
    fake = Fake()
    resolved, _ = provider.dialect()
    runner._client = client  # type: ignore[assignment]
    runner._limiter = RateLimiter(
        resolved, ceilings or {}, clock=fake.clock, sleep=fake.sleep
    )
    return runner, fake


def send(
    runner: OpenAIAgentRunner,
    events: list[tuple[str, str]] | None = None,
    prompt_tokens: int = 2000,
) -> object:
    """One request of roughly the asked-for size, since the estimate is what the
    limiter sees before the gateway answers."""
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
    runner, fake = runner_with(client, tmp_path, {UNCACHED: 3500})

    send(runner)
    send(runner)

    assert client.sent == 2
    assert fake.slept == []


def test_without_settling_the_second_request_would_have_waited(tmp_path: Path) -> None:
    # The same two requests, with the gateway reporting no cache hits at all
    client = StubClient(completion(prompt=5000, cached=0, generated=100))
    runner, fake = runner_with(client, tmp_path, {UNCACHED: 3500})

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
    assert tuple(kind for kind, _ in events if kind == "retry") == ("retry",)
    assert "holding every agent 30s" in events[0][1]
    # The retry itself waited out the hold rather than asking straight away
    assert fake.slept == [pytest.approx(30.0)]


def test_the_ceilings_the_gateway_advertises_are_announced_once(tmp_path: Path) -> None:
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
    runner, fake = runner_with(client, tmp_path, {PROMPT: 3000})
    events: list[tuple[str, str]] = []

    send(runner, events)
    send(runner, events)

    assert [kind for kind, _ in events] == ["paced"]
    assert "prompt tokens" in events[0][1]
    assert fake.slept


def test_a_gateway_that_reports_what_is_left_is_believed_over_the_estimate(
    tmp_path: Path,
) -> None:
    # Every answer carries the remainder, so the local estimate never decides
    # anything: two requests that the run's own arithmetic says will not fit go
    # through, because the gateway says there is room.
    client = StubClient(
        completion(prompt=5000, generated=100),
        headers={"x-ratelimit-limit-tokens": "6000", "x-ratelimit-remaining-tokens": "5800"},
    )
    runner, fake = runner_with(client, tmp_path, {TOKENS: 6000}, dialect="openai")

    send(runner)
    send(runner)

    assert client.sent == 2
    assert fake.slept == []


def test_a_gateway_that_meters_nothing_never_paces_the_runner(tmp_path: Path) -> None:
    client = StubClient(completion(prompt=500_000, generated=8000))
    runner, fake = runner_with(client, tmp_path, dialect="none")
    events: list[tuple[str, str]] = []

    for _ in range(10):
        send(runner, events)

    assert client.sent == 10
    assert fake.slept == []
    assert [kind for kind, _ in events] == []


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
