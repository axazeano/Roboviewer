"""A finished `ReviewRun` posted on the merge request it reviewed.

`pull_request` says which merge request the job is for, `compose` turns the run
into a body and comments on lines, `forge` is what a forge is asked to do, and
`github` is the one that does it. Composing and sending are apart so the first
can be tested and printed without a network.

Reachable from `cli` alone: a review must run with no forge, no token and no
network.
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
