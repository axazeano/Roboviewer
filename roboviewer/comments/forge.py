"""What a forge is asked to do, in the one shape every forge fits.

One call: put a `Draft` on the merge request a `PullRequest` names. Whatever a
forge disagrees about is behind it; a caller sees a URL or a `ForgeError`.

A second forge is a class with that method and a branch in `forge_for`. No
caller names a forge — the job's own variables do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .compose import Draft
from .pull_request import GITHUB, PullRequest, token_variables


class ForgeError(RuntimeError):
    """A review that did not reach the merge request, in the user's terms.

    Every message names what to change: a variable to set, a permission to
    grant, a number that does not exist.
    """


@dataclass(frozen=True)
class Posted:
    """Where the review landed, and what it cost to get it there.

    `comments` is how many remarks went on lines, not how many were offered: a
    forge that refuses the anchors still takes the body. `note` says so.
    """

    url: str
    comments: int
    note: str = ""


class Forge(Protocol):
    """One review onto one merge request."""

    def post(self, pull: PullRequest, draft: Draft) -> Posted: ...


def forge_for(pull: PullRequest, token: str) -> Forge:
    """Whichever forge the job is running on."""
    if pull.forge == GITHUB:
        # Imported here so the forges do not have to know about each other, and
        # so this module stays the contract rather than a list of clients.
        from .github import GitHubForge

        return GitHubForge(token=token)
    raise ForgeError(f"Nothing here can post to {pull.name}.")


def missing_token(pull: PullRequest) -> ForgeError:
    """Said in one place, because it is the failure a first run actually hits."""
    variables = token_variables(pull.forge)
    where = f" Set {' or '.join(variables)}." if variables else ""
    return ForgeError(f"No token for {pull.name}, so there is nothing to post as.{where}")
