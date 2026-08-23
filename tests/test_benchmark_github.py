"""Reading review comments off GitHub, without going near it.

Every request goes through an injected transport, so what is tested here is the
part that actually decides whether the corpus is any good: which line a comment
is pinned to, whether the thread was resolved, and what the command says when it
is the rate limit rather than the entry that stopped it.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from roboviewer.benchmark.github import (
    GitHub,
    GitHubError,
    RateLimited,
    Response,
    resolve_token,
    token_from_env,
    token_from_gh,
)
from roboviewer.benchmark.items import parse_pull_url

PULL = parse_pull_url("https://github.com/psf/requests/pull/6800")

REST_COMMENTS = [
    {
        "id": 1,
        "path": "src/cart.py",
        "line": 90,
        "original_line": 42,
        "body": "This races with the refresh above.",
        "user": {"login": "reviewer"},
        "created_at": "2026-05-01T10:00:00Z",
        "html_url": "https://github.com/psf/requests/pull/6800#discussion_r1",
    },
    {
        "id": 2,
        "in_reply_to_id": 1,
        "path": "src/cart.py",
        "line": 90,
        "original_line": 42,
        "body": "Good catch, fixed.",
        "user": {"login": "author"},
        "created_at": "2026-05-01T11:00:00Z",
        "html_url": "https://github.com/psf/requests/pull/6800#discussion_r2",
    },
    {
        "id": 3,
        "path": "src/api.py",
        "original_line": 7,
        "body": "Naming.",
        "user": {"login": "reviewer"},
        "created_at": "2026-05-01T12:00:00Z",
        "html_url": "https://github.com/psf/requests/pull/6800#discussion_r3",
    },
]

GRAPHQL_THREADS = {
    "data": {
        "repository": {
            "pullRequest": {
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "isResolved": True,
                            "path": "src/cart.py",
                            "line": 90,
                            "originalLine": 42,
                            "comments": {
                                "nodes": [
                                    {
                                        "author": {"login": "reviewer"},
                                        "body": "This races with the refresh above.",
                                        "createdAt": "2026-05-01T10:00:00Z",
                                        "url": "https://github.com/x#r1",
                                    }
                                ]
                            },
                        },
                        {
                            "isResolved": False,
                            "path": "src/api.py",
                            "line": None,
                            "originalLine": 7,
                            "comments": {"nodes": []},
                        },
                    ],
                }
            }
        }
    }
}


class Recorder:
    """A transport that answers from a script and remembers what it was asked."""

    def __init__(self, *answers: Any, status: int = 200, headers: dict[str, str] | None = None):
        self.answers = list(answers)
        self.status = status
        self.headers = headers or {}
        self.urls: list[str] = []
        self.sent: list[dict[str, str]] = []

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes | None  # noqa: ARG002
    ) -> Response:
        self.urls.append(url)
        self.sent.append(headers)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return self.status, self.headers, json.dumps(answer).encode()


def refusing(status: int, headers: dict[str, str], payload: Any = None) -> Recorder:
    return Recorder(payload if payload is not None else {"message": "no"},
                    status=status, headers=headers)


# ------------------------------------------------------------------ with a token


def test_a_token_buys_the_thread_resolution_rest_cannot_give() -> None:
    transport = Recorder(GRAPHQL_THREADS)

    threads = GitHub(token="t", transport=transport).review_threads(PULL)

    assert [t.resolved for t in threads] == [True, False]
    assert transport.urls == ["https://api.github.com/graphql"]


def test_the_token_is_sent_and_only_when_there_is_one() -> None:
    with_token = Recorder(GRAPHQL_THREADS)
    GitHub(token="secret", transport=with_token).review_threads(PULL)
    assert with_token.sent[0]["Authorization"] == "Bearer secret"

    without = Recorder(REST_COMMENTS, [])
    GitHub(token=None, transport=without).review_threads(PULL)
    assert "Authorization" not in without.sent[0]


def test_the_token_is_read_from_either_variable_the_gh_cli_uses() -> None:
    assert token_from_env({"GITHUB_TOKEN": "one"}) == "one"
    assert token_from_env({"GH_TOKEN": "two"}) == "two"
    assert token_from_env({"GITHUB_TOKEN": "  ", "GH_TOKEN": "two"}) == "two"
    assert token_from_env({}) is None


def test_a_pull_request_the_token_cannot_see_is_named_as_such() -> None:
    transport = Recorder({"data": {"repository": None}})

    with pytest.raises(GitHubError, match="psf/requests#6800 was not found"):
        GitHub(token="t", transport=transport).review_threads(PULL)


# ------------------------------------------------------------------ without one


def test_without_a_token_the_comments_still_arrive_but_resolution_is_unknown() -> None:
    client = GitHub(token=None, transport=Recorder(REST_COMMENTS, []))

    threads = client.review_threads(PULL)

    assert not client.resolution_known
    # Unknown, not False: nobody asked, rather than nobody resolved it
    assert [t.resolved for t in threads] == [None, None]


def test_a_reply_joins_the_thread_it_answers_rather_than_starting_one() -> None:
    threads = GitHub(token=None, transport=Recorder(REST_COMMENTS, [])).review_threads(PULL)

    assert [t.file for t in threads] == ["src/cart.py", "src/api.py"]
    assert [c.author for c in threads[0].comments] == ["reviewer", "author"]
    assert threads[0].comments[0].body == "This races with the refresh above."


def test_a_comment_is_pinned_to_the_line_reviewers_saw() -> None:
    threads = GitHub(token=None, transport=Recorder(REST_COMMENTS, [])).review_threads(PULL)

    # 42 is where it stood in the head under review; 90 is where the line drifted
    # to afterwards, and the clone is not positioned there
    assert threads[0].line == 42


# ------------------------------------------------------------------ when it stops


def test_the_rate_limit_is_named_as_the_reason_and_so_is_the_way_out() -> None:
    transport = refusing(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"})

    with pytest.raises(RateLimited) as caught:
        GitHub(token=None, transport=transport).review_threads(PULL)

    message = str(caught.value)
    assert "rate limit" in message
    assert "GITHUB_TOKEN" in message
    assert "resets at" in message


def test_a_rate_limited_token_is_told_it_is_the_token_that_ran_out() -> None:
    transport = refusing(403, {"x-ratelimit-remaining": "0"})

    with pytest.raises(RateLimited, match="for this token"):
        GitHub(token="t", transport=transport).review_threads(PULL)


def test_graphql_reports_the_rate_limit_inside_a_200() -> None:
    transport = Recorder({"errors": [{"type": "RATE_LIMITED", "message": "slow down"}]})

    with pytest.raises(RateLimited):
        GitHub(token="t", transport=transport).review_threads(PULL)


def test_a_403_that_is_not_the_rate_limit_is_not_reported_as_one() -> None:
    transport = refusing(403, {}, {"message": "Resource not accessible"})

    with pytest.raises(GitHubError) as caught:
        GitHub(token="t", transport=transport).review_threads(PULL)

    assert not isinstance(caught.value, RateLimited)
    assert "Resource not accessible" in str(caught.value)


def test_a_refused_token_says_which_variable_to_look_at() -> None:
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        GitHub(token="stale", transport=refusing(401, {})).review_threads(PULL)


# --------------------------------------------------------------- where a token comes from


def gh_answering(stdout: str = "", returncode: int = 0):
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command == ["gh", "auth", "token"]
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    return runner


def test_the_login_gh_already_holds_is_used_when_the_environment_is_empty() -> None:
    """`gh` keeps its token in a keyring, not in the environment. Without this,
    the answer to "you need a token" is to copy one out of `gh` into a shell
    profile — a worse place for it than where it already is."""
    assert resolve_token({}, gh_answering("gho_fromkeyring\n")) == "gho_fromkeyring"


def test_an_explicit_variable_beats_whoever_is_logged_in() -> None:
    """A variable is a deliberate choice: a CI job, or a second account."""
    assert resolve_token({"GITHUB_TOKEN": "explicit"}, gh_answering("gho_other")) == "explicit"


def test_every_way_gh_can_fail_is_the_same_answer() -> None:
    """Missing, not logged in, or hanging: the caller has a good next step for
    all three, and none of them is worth an error."""
    assert token_from_gh(gh_answering(returncode=1)) is None
    assert token_from_gh(gh_answering(stdout="  \n")) is None

    def missing(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    def hanging(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 5.0)

    assert token_from_gh(missing) is None
    assert token_from_gh(hanging) is None
    assert resolve_token({}, missing) is None
