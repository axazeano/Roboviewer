"""What GitHub knows about a pull request: its facts, and what reviewers said.

Two APIs, because thread resolution exists in only one of them. GraphQL knows
whether a thread was marked resolved and needs a token for every request; REST
answers without one at sixty requests an hour and has never exposed resolution.
So: with a token the threads come from GraphQL complete, without a token they
come from REST with resolution unknown, and the saved file says which.

Both shapes are normalised into `Thread` here, so nothing downstream has to know
which door the data came through. HTTP goes through an injectable callable —
that is the whole reason the tests need no network.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .items import PullRequest

API_URL = "https://api.github.com"
# In the order the GitHub CLI reads them, so a machine already set up for `gh`
# needs nothing else — and where `gh` keeps its login outside the environment,
# `token_from_gh` asks it.
TOKEN_VARS = ("GITHUB_TOKEN", "GH_TOKEN")
GH_TOKEN_COMMAND = ("gh", "auth", "token")
GH_TIMEOUT_S = 5.0
PAGE_SIZE = 100
# A stop for the paging loops. Nothing in the benchmark is near it; a pull request
# that is would be a discussion, not a review.
MAX_PAGES = 20
TIMEOUT_S = 30.0

# (status, lowercased headers, body)
Response = tuple[int, dict[str, str], bytes]
Transport = Callable[[str, dict[str, str], bytes | None], Response]
# How `gh` is asked for its token. Injectable for the same reason the transport
# is: a test that shells out is a test that depends on the machine.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          path
          line
          originalLine
          comments(first: 100) {
            nodes { author { login } body createdAt url originalCommit { oid } }
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created_at: str
    url: str


@dataclass(frozen=True)
class Thread:
    """One conversation anchored at a line of one commit.

    `line` is the line in the head reviewers saw, not in whatever the branch
    became afterwards: the clone is positioned at that head, so GitHub's
    `originalLine` is the one that lines up with the file on disk.

    `commit` is that head, as GitHub recorded it — the commit this thread was
    written against. An entry whose head is a later commit has the review
    pointing at code the author has already changed.
    """

    file: str
    line: int | None
    resolved: bool | None
    comments: list[Comment] = field(default_factory=list)
    commit: str = ""


@dataclass(frozen=True)
class Facts:
    """The pull request as the REST API describes it: the two branch tips, the
    size, and what the repository is. `head` is the tip of the branch, which is
    not the commit reviewers saw whenever the author pushed fixes afterwards —
    see `candidates.on_github.propose_head` for the one that is."""

    url: str
    number: int
    base: str
    head: str
    files: int
    added: int
    removed: int
    stars: int
    language: str
    license: str


class GitHubError(RuntimeError):
    """A request that did not produce review comments, in the user's terms."""


class RateLimited(GitHubError):
    """Stopped by the rate limit rather than by anything about the entry."""


@dataclass
class GitHub:
    """Review threads for a pull request, with or without a token."""

    token: str | None = None
    api_url: str = API_URL
    transport: Transport | None = None

    @property
    def resolution_known(self) -> bool:
        """Whether this client can say if a thread was resolved. Recorded next
        to the comments, so a later reader is not left guessing why the field
        is null on half the benchmark."""
        return self.token is not None

    def graphql(self, query: str, variables: dict[str, Any]) -> Any:
        """One GraphQL request, for callers that ask something other than review
        threads. Errors surface the same way they do everywhere else here."""
        body = json.dumps({"query": query, "variables": variables}).encode()
        return self._call(f"{self.api_url}/graphql", body=body)

    def pull_request(self, pull: PullRequest) -> Facts:
        """The facts of one pull request. REST, because it answers without a
        token and carries everything the index records."""
        url = f"{self.api_url}/repos/{pull.owner}/{pull.repo}/pulls/{pull.number}"
        raw = self._call(url)
        if not isinstance(raw, dict) or "base" not in raw or "head" not in raw:
            raise GitHubError(f"{pull.slug}#{pull.number}: unexpected answer from {url}")
        repository = (raw.get("base") or {}).get("repo") or {}
        return Facts(
            url=raw.get("html_url") or f"https://github.com/{pull.slug}/pull/{pull.number}",
            number=int(raw.get("number") or pull.number),
            base=str((raw.get("base") or {}).get("sha") or ""),
            head=str((raw.get("head") or {}).get("sha") or ""),
            files=int(raw.get("changed_files") or 0),
            added=int(raw.get("additions") or 0),
            removed=int(raw.get("deletions") or 0),
            stars=int(repository.get("stargazers_count") or 0),
            language=str(repository.get("language") or ""),
            license=str((repository.get("license") or {}).get("spdx_id") or ""),
        )

    def review_threads(self, pull: PullRequest) -> list[Thread]:
        if self.token:
            return self._threads_via_graphql(pull)
        return _threads_from_rest(self._rest_comments(pull))

    def _threads_via_graphql(self, pull: PullRequest) -> list[Thread]:
        threads: list[Thread] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            variables = {
                "owner": pull.owner,
                "name": pull.repo,
                "number": pull.number,
                "cursor": cursor,
            }
            body = json.dumps({"query": THREADS_QUERY, "variables": variables}).encode()
            payload = self._call(f"{self.api_url}/graphql", body=body)
            page = _graphql_review_threads(payload, pull)
            threads.extend(_thread_from_graphql(node) for node in page["nodes"])
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
        return threads

    def _rest_comments(self, pull: PullRequest) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            url = (
                f"{self.api_url}/repos/{pull.owner}/{pull.repo}/pulls/{pull.number}"
                f"/comments?per_page={PAGE_SIZE}&page={page}"
            )
            batch = self._call(url)
            if not isinstance(batch, list):
                raise GitHubError(f"{pull.slug}#{pull.number}: unexpected answer from {url}")
            comments.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return comments

    def _call(self, url: str, body: bytes | None = None) -> Any:
        transport = self.transport or _urllib_transport
        status, headers, raw = transport(url, self._headers(body is not None), body)
        if status != 200:
            raise _http_error(url, status, headers, raw, token=self.token)
        payload = json.loads(raw or b"null")
        if isinstance(payload, dict) and payload.get("errors"):
            raise _graphql_error(payload["errors"], token=self.token)
        return payload

    def _headers(self, sending_body: bool) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "roboviewer-benchmark",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if sending_body:
            headers["Content-Type"] = "application/json"
        return headers


def token_from_env(environ: dict[str, str] | None = None) -> str | None:
    """The first token variable that is set to something."""
    source = os.environ if environ is None else environ
    for name in TOKEN_VARS:
        value = source.get(name, "").strip()
        if value:
            return value
    return None


def token_from_gh(runner: Runner | None = None) -> str | None:
    """The login `gh` is already holding, or None if it is not there to ask.

    Most machines that have a reason to build a benchmark have `gh` set up, and its
    token lives in a keyring rather than in the environment — so without this,
    the answer to "you need a token" is to copy one out of `gh` and paste it into
    a shell profile, which is a worse place for it than where it already is.

    Every failure is the same answer: `gh` missing, `gh` not logged in, `gh`
    hanging. None of them is worth an error, because the caller has a perfectly
    good next step either way.
    """
    run = runner or _run_gh
    try:
        completed = run(list(GH_TOKEN_COMMAND))
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def resolve_token(
    environ: dict[str, str] | None = None, runner: Runner | None = None
) -> str | None:
    """The environment first, then `gh`. An explicit variable is a deliberate
    choice — a CI job, or a second account — and beats whoever is logged in."""
    return token_from_env(environ) or token_from_gh(runner)


def _run_gh(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=GH_TIMEOUT_S, check=False
    )


def _threads_from_rest(comments: list[dict[str, Any]]) -> list[Thread]:
    """REST returns a flat list; the thread is `in_reply_to_id` pointing back at
    the comment that started it."""
    roots: dict[int, Thread] = {}
    order: list[int] = []
    for raw in comments:
        root_id = raw.get("in_reply_to_id") or raw.get("id")
        if root_id is None:
            continue
        if root_id not in roots:
            roots[root_id] = Thread(
                file=raw.get("path") or "",
                line=_line_of(raw),
                # REST has never carried it, and inventing False would read as
                # "nobody resolved this" rather than "nobody asked".
                resolved=None,
                commit=str(raw.get("original_commit_id") or ""),
            )
            order.append(root_id)
        roots[root_id].comments.append(
            Comment(
                author=(raw.get("user") or {}).get("login") or "",
                body=raw.get("body") or "",
                created_at=raw.get("created_at") or "",
                url=raw.get("html_url") or "",
            )
        )
    return [roots[root_id] for root_id in order]


def _thread_from_graphql(node: dict[str, Any]) -> Thread:
    raw_comments = (node.get("comments") or {}).get("nodes") or []
    first = raw_comments[0] if raw_comments else {}
    return Thread(
        file=node.get("path") or "",
        line=node.get("originalLine") or node.get("line"),
        resolved=node.get("isResolved"),
        commit=str((first.get("originalCommit") or {}).get("oid") or ""),
        comments=[
            Comment(
                author=(raw.get("author") or {}).get("login") or "",
                body=raw.get("body") or "",
                created_at=raw.get("createdAt") or "",
                url=raw.get("url") or "",
            )
            for raw in raw_comments
        ],
    )


def _graphql_review_threads(payload: Any, pull: PullRequest) -> dict[str, Any]:
    repository = (payload or {}).get("data", {}).get("repository")
    request = (repository or {}).get("pullRequest")
    if request is None:
        raise GitHubError(
            f"{pull.slug}#{pull.number} was not found. A private repository, a "
            "wrong number, or a token without access to it."
        )
    return request["reviewThreads"]


def _line_of(raw: dict[str, Any]) -> int | None:
    """The line in the head reviewers commented on, not in the merged result."""
    for key in ("original_line", "line", "original_start_line"):
        value = raw.get(key)
        if isinstance(value, int):
            return value
    return None


def _http_error(
    url: str, status: int, headers: dict[str, str], raw: bytes, *, token: str | None
) -> GitHubError:
    if _is_rate_limit(status, headers):
        return RateLimited(_rate_limit_message(headers, token=token))
    if status == 401:
        return GitHubError(
            f"GitHub refused the token ({url}). "
            f"Check {' or '.join(TOKEN_VARS)}, or unset it to read anonymously."
        )
    if status == 404:
        return GitHubError(
            f"GitHub has no {url} — a private repository, or a pull request "
            "number that does not exist."
        )
    detail = _message_in(raw)
    return GitHubError(f"GitHub answered {status} for {url}{': ' + detail if detail else ''}")


def _graphql_error(errors: list[dict[str, Any]], *, token: str | None) -> GitHubError:
    """GraphQL reports failure inside a 200, rate limiting included."""
    if any(error.get("type") == "RATE_LIMITED" for error in errors):
        return RateLimited(_rate_limit_message({}, token=token))
    messages = "; ".join(str(error.get("message", error)) for error in errors)
    return GitHubError(f"GitHub GraphQL error: {messages}")


def _is_rate_limit(status: int, headers: dict[str, str]) -> bool:
    if status not in (403, 429):
        return False
    # 403 is also plain "no access", and the two need different answers
    return headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers


def _rate_limit_message(headers: dict[str, str], *, token: str | None) -> str:
    reset = _reset_at(headers)
    when = f" It resets at {reset}." if reset else ""
    if token:
        return (
            f"GitHub rate limit reached for this token.{when} "
            "Rerun later; what is already fetched is kept."
        )
    return (
        f"GitHub rate limit reached for anonymous requests (60 an hour).{when} "
        f"Set {TOKEN_VARS[0]} to raise it; what is already fetched is kept."
    )


def _reset_at(headers: dict[str, str]) -> str:
    raw = headers.get("x-ratelimit-reset") or ""
    if not raw.isdigit():
        return ""
    return time.strftime("%H:%M:%S", time.localtime(int(raw)))


def _message_in(raw: bytes) -> str:
    try:
        payload = json.loads(raw or b"null")
    except ValueError:
        return ""
    return str(payload.get("message", "")) if isinstance(payload, dict) else ""


def _urllib_transport(url: str, headers: dict[str, str], body: bytes | None) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, _lowercased(dict(response.headers)), response.read()
    except urllib.error.HTTPError as exc:
        # A 403 carries the rate-limit headers, so the body and headers of a
        # failure are as interesting as those of a success
        return exc.code, _lowercased(dict(exc.headers or {})), exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubError(f"Could not reach {url}: {exc}") from exc


def _lowercased(headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}
