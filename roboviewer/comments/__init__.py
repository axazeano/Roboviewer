"""Putting a finished review where the discussion is.

The review path never learns what a pull request is. It reads two git branches
and produces a `ReviewRun`; this package takes that finished run and posts it,
and it is reachable from `cli` alone. Nothing under `reports` imports it, which
is what keeps a review runnable on a laptop with no forge, no token and no
network.

Named for what lands, the way `reports` is: a run turns into files there and
into remarks on a merge request here. What it talks to is a forge — GitHub, and
one day GitLab — and naming the package after either would be a lie about the
other, so that word names the counterpart inside instead.

Four modules, in the order a caller meets them: `pull_request` says which merge
request the job is running for, `compose` turns a run into the body and the
comments that would be posted, `forge` is the one thing a forge is asked to do,
and `github` is the forge that does it. Composing and sending are apart because
only the second needs a network, and only the first needs to know what a finding
is.
"""

from __future__ import annotations

from .compose import Draft, LineComment, compose
from .forge import Forge, ForgeError, Posted, forge_for, missing_token
from .pull_request import GITHUB, PullRequest, detect, on_github, token_for

__all__ = [
    "GITHUB",
    "Draft",
    "Forge",
    "ForgeError",
    "LineComment",
    "Posted",
    "PullRequest",
    "compose",
    "detect",
    "forge_for",
    "missing_token",
    "on_github",
    "token_for",
]
