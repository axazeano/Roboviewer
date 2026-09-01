"""Which merge request a job is running for, read out of its environment.

The repository, the number and the API address come from the variables the
runner sets; a flag overrides them.

`cli.ci_env` answers a different question from the same environment — which
branch is merged into — and shares no variable with this.

The token is not a field here: the coordinates are printed in the job log.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# The key a forge is chosen by. A second forge adds a constant here, a
# reader below and a class in its own module.
GITHUB = "github"

# refs/pull/<number>/merge on a pull_request event; a branch build has no number
# and no pull request to comment on.
_PULL_REF = "refs/pull/"
_GITHUB_API = "https://api.github.com"
# In the order the GitHub CLI reads them, so a machine already set up for `gh`
# needs nothing else. GITHUB_TOKEN is what Actions injects into a job.
_GITHUB_TOKENS = ("GITHUB_TOKEN", "GH_TOKEN")


@dataclass(frozen=True)
class PullRequest:
    """The merge request a job is running for.

    `forge` is which forge to post through, `name` what the log line says.
    `api_url` comes from the environment, so an Enterprise installation needs
    no flag.
    """

    forge: str
    name: str
    slug: str
    number: int
    api_url: str


def detect(environ: Mapping[str, str] | None = None) -> PullRequest | None:
    """The pull request this job is for, or None outside one.

    None is an ordinary answer — a push build, a laptop, a tag pipeline — so
    nothing here raises; the caller says what is missing.
    """
    env = os.environ if environ is None else environ
    for read in _READERS:
        pull = read(env)
        if pull is not None:
            return pull
    return None


def on_github(slug: str, number: int, api_url: str = "") -> PullRequest:
    """A pull request named by hand rather than found in a pipeline.

    GitHub because it is the forge that is implemented; a second one would add
    a way to say which.
    """
    return PullRequest(
        forge=GITHUB,
        name="GitHub",
        slug=slug,
        number=number,
        api_url=api_url or _GITHUB_API,
    )


def token_for(forge: str, environ: Mapping[str, str] | None = None) -> str | None:
    """The token this forge is written to with, out of the environment.

    Environment only: a login found elsewhere on the machine would post as
    somebody who never asked to.
    """
    env = os.environ if environ is None else environ
    for name in _TOKEN_VARS.get(forge, ()):
        value = env.get(name, "").strip()
        if value:
            return value
    return None


def token_variables(forge: str) -> tuple[str, ...]:
    """What to tell someone to set when there is no token."""
    return _TOKEN_VARS.get(forge, ())


def _github(env: Mapping[str, str]) -> PullRequest | None:
    slug = env.get("GITHUB_REPOSITORY", "").strip()
    number = _pull_number(env.get("GITHUB_REF", ""))
    if not slug or number is None:
        return None
    return PullRequest(
        forge=GITHUB,
        name="GitHub Actions",
        slug=slug,
        number=number,
        api_url=env.get("GITHUB_API_URL", "").strip() or _GITHUB_API,
    )


def _pull_number(ref: str) -> int | None:
    """`refs/pull/42/merge` → 42. Anything else is not a pull request build."""
    ref = ref.strip()
    if not ref.startswith(_PULL_REF):
        return None
    number = ref[len(_PULL_REF) :].split("/", 1)[0]
    return int(number) if number.isdigit() else None


_READERS = (_github,)
_TOKEN_VARS: dict[str, tuple[str, ...]] = {GITHUB: _GITHUB_TOKENS}
