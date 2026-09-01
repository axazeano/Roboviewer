"""Posting a review to a GitHub pull request.

One request, one review, as an event of kind COMMENT — not REQUEST_CHANGES:
whether a branch may merge is a person's call.

GitHub refuses the whole review with a 422 when one anchored comment names a
line its diff does not carry, so that answer is caught and the body posted
alone.

HTTP goes through an injectable callable, so the tests need no network.
`benchmark.github` has its own client: it reads where this one writes, and
nothing on the review path may depend on the benchmark.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .compose import Draft
from .forge import ForgeError, Posted
from .pull_request import PullRequest, token_variables

# (status, body)
Response = tuple[int, bytes]
Transport = Callable[[str, dict[str, str], bytes], Response]

TIMEOUT_S = 30.0
# A review is a remark, not a verdict on whether the branch may merge.
EVENT = "COMMENT"
# The new version of the file — the side a finding about added code is on.
SIDE = "RIGHT"


@dataclass
class GitHubForge:
    """A review onto a pull request, with or without its anchored comments."""

    token: str
    transport: Transport | None = None

    def post(self, pull: PullRequest, draft: Draft) -> Posted:
        url = f"{pull.api_url}/repos/{pull.slug}/pulls/{pull.number}/reviews"
        payload = {
            "body": draft.body,
            "event": EVENT,
            "comments": [
                {"path": c.file, "line": c.line, "side": SIDE, "body": c.body}
                for c in draft.comments
            ],
        }
        status, raw = self._send(url, payload)
        if status in (200, 201):
            return Posted(url=_review_url(raw, pull), comments=len(draft.comments))
        if status == 422 and draft.comments:
            return self._body_alone(url, pull, draft, _detail(raw))
        raise _refused(url, status, raw, pull)

    def _body_alone(
        self, url: str, pull: PullRequest, draft: Draft, detail: str
    ) -> Posted:
        """What is left when GitHub will not take the anchors: the prose, which
        names every finding anyway."""
        status, raw = self._send(url, {"body": draft.body, "event": EVENT})
        if status not in (200, 201):
            raise _refused(url, status, raw, pull)
        return Posted(
            url=_review_url(raw, pull),
            comments=0,
            note=(
                f"GitHub refused the {len(draft.comments)} line comments "
                f"({detail or '422'}); the body went up carrying all of them."
            ),
        )

    def _send(self, url: str, payload: Mapping[str, object]) -> Response:
        transport = self.transport or _urllib_transport
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "roboviewer",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        return transport(url, headers, json.dumps(payload).encode())


def _review_url(raw: bytes, pull: PullRequest) -> str:
    """Where the review landed. GitHub says so; if it did not, the pull request
    itself is a better answer than nothing."""
    try:
        payload = json.loads(raw or b"null")
    except ValueError:
        payload = None
    if isinstance(payload, dict) and payload.get("html_url"):
        return str(payload["html_url"])
    return f"https://github.com/{pull.slug}/pull/{pull.number}"


def _refused(url: str, status: int, raw: bytes, pull: PullRequest) -> ForgeError:
    detail = _detail(raw)
    variables = " or ".join(token_variables(pull.forge))
    if status == 401:
        return ForgeError(
            f"GitHub refused the token. Check {variables} — it is set, so it is "
            "the wrong one or it has expired."
        )
    if status == 403:
        return ForgeError(
            f"The token may not write to {pull.slug}. A review needs "
            "`pull-requests: write`; add it to the job's permissions."
        )
    if status == 404:
        return ForgeError(
            f"GitHub has no pull request {pull.number} in {pull.slug} — "
            "a wrong number, or a token that cannot see the repository."
        )
    if status == 422:
        return ForgeError(f"GitHub would not take the review: {detail or 'it was rejected'}.")
    return ForgeError(f"GitHub answered {status} for {url}{': ' + detail if detail else ''}")


def _detail(raw: bytes) -> str:
    """The sentence GitHub puts on a failure, if it put one there."""
    try:
        payload = json.loads(raw or b"null")
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    message = str(payload.get("message", ""))
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        said = first.get("message") if isinstance(first, dict) else None
        if said:
            return f"{message}: {said}" if message else str(said)
    return message


def _urllib_transport(url: str, headers: dict[str, str], body: bytes) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        # The body of a failure is where GitHub says which comment it disliked
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ForgeError(f"Could not reach {url}: {exc}") from exc
