"""The pull request a search yielded, before anybody has judged it.

The word the whole package is built on, so it lives on its own: `on_github`
produces these, `criteria` holds them to a bar, `entry_toml` writes one down.
A candidate is facts and nothing else — no verdict about it is stored here,
because deciding is what the other three do.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..items import parse_pull_url


@dataclass(frozen=True)
class Candidate:
    """One pull request a search yielded, with the facts the criteria ask about.

    `base` comes back with the search; `head` does not, because deriving it
    costs a request of its own — see `on_github.propose_head`.
    """

    url: str
    slug: str
    number: int
    files: int
    added: int
    removed: int
    threads: int
    stars: int
    language: str
    license: str
    base: str

    @property
    def id(self) -> str:
        """The directory name this entry would build into: repo and number, the
        shape the index already uses."""
        return parse_pull_url(self.url).entry_id
