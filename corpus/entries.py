"""The committed list of pull requests, and what an entry has to say.

One file describes the corpus, and it is the reason a rebuild does not depend on
anyone remembering which commit was the right head. Four fields are what the
fetcher reads — id, url, base, head; the rest is what a later reader needs to
judge whether an entry earns its place, and is carried here rather than in a
second file so the two cannot drift apart.

Extra keys are refused, for the same reason `roboviewer.config` refuses them: a
key nobody reads is a field somebody believed they had set.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from roboviewer.config import STRICT

# github.com/<owner>/<repo>/pull/<number>, with whatever tab or anchor the URL
# was copied with. Only github.com: another forge means another API, and
# guessing which one from a hostname would fail later and less clearly.
PULL_URL = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:[/#?].*)?$"
)
# Used as a directory name under the corpus root, so nothing that can climb out
# of it or collide on a case-insensitive filesystem.
ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SHA = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class PullRequest:
    """The three parts of the URL an API call needs."""

    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"


class Entry(BaseModel):
    """One pull request in the corpus.

    `base` and `head` are full SHAs on purpose. A branch name moves, a short SHA
    is ambiguous, and the head that matters is the one reviewers saw — usually
    not the merged head, where the defects they found are already fixed.
    """

    model_config = STRICT

    # Names the directory the entry is built into, so it stays stable across
    # rebuilds even when the pull request is retitled.
    id: str
    url: str
    base: str
    head: str

    # Everything below is read by people, not by the fetcher: what the review
    # found, and enough about the entry to see whether the corpus leans one way.
    language: str = ""
    domain: str = ""
    found: str = ""
    license: str = ""
    files: int = 0
    added: int = 0
    removed: int = 0

    @property
    def pull(self) -> PullRequest:
        return parse_pull_url(self.url)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not ID.match(value):
            raise ValueError(
                f"id {value!r} is used as a directory name: "
                "lowercase letters, digits, dot, dash and underscore only"
            )
        return value

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        parse_pull_url(value)
        return value

    @field_validator("base", "head")
    @classmethod
    def _check_sha(cls, value: str) -> str:
        if not SHA.match(value):
            raise ValueError(
                f"{value!r} is not a full 40-character commit SHA — "
                "a branch name moves and a short SHA is ambiguous"
            )
        return value.lower()


class CorpusList(BaseModel):
    """The file as a whole: `[[entry]]` tables, in the order they were written."""

    model_config = STRICT

    entry: list[Entry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> CorpusList:
        seen: set[str] = set()
        for entry in self.entry:
            if entry.id in seen:
                raise ValueError(f"two entries share the id {entry.id!r}; ids name directories")
            seen.add(entry.id)
        return self


def parse_pull_url(url: str) -> PullRequest:
    match = PULL_URL.match(url.strip())
    if match is None:
        raise ValueError(
            f"{url!r} is not a GitHub pull request URL "
            "(https://github.com/<owner>/<repo>/pull/<number>)"
        )
    return PullRequest(
        owner=match["owner"],
        repo=match["repo"].removesuffix(".git"),
        number=int(match["number"]),
    )


def load_list(path: Path) -> list[Entry]:
    """The entries in the file, or a message saying what is wrong with it."""
    resolved = path.expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"Corpus list not found: {resolved}")
    with resolved.open("rb") as fh:
        raw = tomllib.load(fh)
    try:
        parsed = CorpusList.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{resolved}:\n{exc}") from exc
    if not parsed.entry:
        raise ValueError(f"{resolved} lists no entries; each one is an [[entry]] table")
    return parsed.entry


def select(entries: list[Entry], only: str | None) -> list[Entry]:
    """`--only a,b` narrows the list, and an id that is not in it is a typo
    rather than a request for nothing."""
    if not only:
        return entries
    wanted = [name.strip() for name in only.split(",") if name.strip()]
    by_id = {entry.id: entry for entry in entries}
    missing = [name for name in wanted if name not in by_id]
    if missing:
        raise ValueError(f"no such entries: {', '.join(missing)}")
    return [by_id[name] for name in wanted]
