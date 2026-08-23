"""The index of merge requests, and what an entry has to say.

One file, `items.toml`, describes the benchmark, and it is the reason a rebuild
does not depend on anyone remembering which commit was the right head. Four
fields are what the fetcher reads — id, url, base, head; the rest is what a
later reader needs to judge whether an entry earns its place, and is carried
here rather than in a second file so the two cannot drift apart.

`benchmark list add` and `list remove` edit the file as text, one `[[entry]]`
table at a time, so the comments somebody wrote around the other entries
survive. Extra keys are refused, for the same reason `roboviewer.config`
refuses them: a key nobody reads is a field somebody believed they had set.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..config import STRICT

# github.com/<owner>/<repo>/pull/<number>, with whatever tab or anchor the URL
# was copied with. Only github.com: another forge means another API, and
# guessing which one from a hostname would fail later and less clearly.
PULL_URL = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:[/#?].*)?$"
)
# Used as a directory name under the benchmarks root, so nothing that can climb
# out of it or collide on a case-insensitive filesystem.
ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SHA = re.compile(r"^[0-9a-fA-F]{40}$")
TABLE = "[[entry]]"


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

    @property
    def entry_id(self) -> str:
        """The directory name an entry for this pull request builds into: repo
        and number, the shape the index already uses."""
        return f"{self.repo.lower()}-{self.number}"


class Entry(BaseModel):
    """One merge request in the benchmark.

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
    # found, and enough about the entry to see whether the benchmark leans one
    # way.
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


class Items(BaseModel):
    """The file as a whole: `[[entry]]` tables, in the order they were written."""

    model_config = STRICT

    entry: list[Entry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Items:
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


def load_items(path: Path, *, allow_empty: bool = False) -> list[Entry]:
    """The entries in the file, or a message saying what is wrong with it.

    A missing or empty index is an error for every command but `list add`,
    which is how the file comes to exist in the first place.
    """
    resolved = path.expanduser()
    if not resolved.is_file():
        if allow_empty:
            return []
        raise FileNotFoundError(f"Index not found: {resolved} — `benchmark list add` creates it")
    with resolved.open("rb") as fh:
        raw = tomllib.load(fh)
    try:
        parsed = Items.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{resolved}:\n{exc}") from exc
    if not parsed.entry and not allow_empty:
        raise ValueError(f"{resolved} lists no entries; each one is an [[entry]] table")
    return parsed.entry


def select(entries: list[Entry], only: str | None) -> list[Entry]:
    """`--entries a,b` narrows the list, and an id that is not in it is a typo
    rather than a request for nothing."""
    if not only:
        return entries
    wanted = [name.strip() for name in only.split(",") if name.strip()]
    by_id = {entry.id: entry for entry in entries}
    missing = [name for name in wanted if name not in by_id]
    if missing:
        raise ValueError(f"no such entries: {', '.join(missing)}")
    return [by_id[name] for name in wanted]


def render(entry: Entry) -> str:
    """The `[[entry]]` table as it is written into the index.

    `domain` and `found` are hinted at when blank: they are the two fields a
    reader uses to decide whether an entry earns its place, and a sentence
    generated from a title would read exactly like one somebody had checked.
    """
    domain = (
        f'domain = "{_escape(entry.domain)}"'
        if entry.domain
        else 'domain = ""    # what this repository is for'
    )
    found = (
        f'found = "{_escape(entry.found)}"'
        if entry.found
        else 'found = ""     # the defect the review found, in one line'
    )
    return "\n".join(
        [
            TABLE,
            f'id = "{entry.id}"',
            f'url = "{entry.url}"',
            f'base = "{entry.base}"',
            f'head = "{entry.head}"',
            f'language = "{entry.language}"',
            domain,
            found,
            f'license = "{entry.license}"',
            f"files = {entry.files}",
            f"added = {entry.added}",
            f"removed = {entry.removed}",
            "",
        ]
    )


def append(path: Path, entry: Entry) -> None:
    """Add the entry to the index, refusing a second one for the same id or
    pull request. The rest of the file is left exactly as it was."""
    existing = load_items(path, allow_empty=True)
    for other in existing:
        if other.id == entry.id or other.url == entry.url:
            raise ValueError(f"{other.id} is already in the index ({other.url})")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else _HEADER
    if text and not text.endswith("\n"):
        text += "\n"
    if existing or text.strip():
        text += "\n"
    path.write_text(text + render(entry), encoding="utf-8")


def remove(path: Path, key: str) -> Entry:
    """Take the entry named by id or URL out of the index and return it; every
    other byte of the file stays. Raises `ValueError` when nothing matches."""
    entries = load_items(path)
    key = key.strip()
    matching = [e for e in entries if e.id == key or e.url.rstrip("/") == key.rstrip("/")]
    if not matching:
        raise ValueError(f"no entry with id or url {key!r}")
    entry = matching[0]
    preamble, *blocks = _tables(path.read_text(encoding="utf-8"))
    kept = [block for block in blocks if _id_of(block) != entry.id]
    path.write_text((preamble + "".join(kept)).rstrip("\n") + "\n", encoding="utf-8")
    return entry


_HEADER = (
    "# The merge requests the benchmark reviews: one [[entry]] per pull request.\n"
    "# `benchmark list add <url>` appends here; see docs/benchmark.md.\n"
)


def _tables(text: str) -> list[str]:
    """The file cut at every `[[entry]]` line: what comes before the first, then
    one string per table with its header line. A `[[entry]]` inside a comment
    is not a header, which is why this goes by lines rather than by substring."""
    chunks: list[str] = [""]
    for line in text.splitlines(keepends=True):
        if line.strip() == TABLE:
            chunks.append("")
        chunks[-1] += line
    return chunks


def _id_of(table: str) -> str:
    [parsed] = tomllib.loads(table)["entry"]
    return str(parsed.get("id", ""))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
