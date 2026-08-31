"""What sending a review to a forge amounts to, in the one shape every forge fits.

One call: take a `Draft` and put it on the merge request a `PullRequest` names.
Everything a forge disagrees about — REST or GraphQL, what a comment is anchored
by, which status code means "your token cannot write here" — is behind it, and a
caller sees a URL or a `ForgeError` carrying a sentence somebody can act on.

The seam takes two forges and one is written. Adding GitLab is a class with this
one method and a branch in `forge_for`; no caller changes, because no caller
names a forge — the job's own variables do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .compose import Draft
from .pull_request import GITHUB, PullRequest, token_variables


class ForgeError(RuntimeError):
    """A review that did not reach the merge request, in the user's terms.

    Every message here names what to change: a variable to set, a permission to
    grant, a number that does not exist. A traceback tells a person nothing they
    can act on from inside a job log.
    """


@dataclass(frozen=True)
class Posted:
    """Where the review landed, and what it cost to get it there.

    `comments` is how many remarks actually went on lines rather than how many
    were offered: a forge that refuses the anchored comments still takes the
    body, and `note` is what to say about the difference.
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
