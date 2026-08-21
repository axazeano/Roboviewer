"""Candidates for the corpus, sieved by the size GitHub refuses to search on.

Growing the corpus means finding pull requests of a certain size whose review
found something, and GitHub search cannot express the first half: there is no
`files:` qualifier and no `additions:`. What search does return, through
GraphQL, is `changedFiles` on every pull request it yields — so the filter still
runs on this side, but it costs one request per page instead of one per
candidate.

The head is the part worth being careful about. An entry has to name the commit
reviewers were looking at; the merged head is the one where everything they
found is already fixed, and an entry pointing there measures nothing. GitHub
records, per review thread, the commit it was written against, and `github.py`
already reads it — `propose_head` is that, sorted by time and taken from the
front.

What is deliberately not here is the judgement `docs/corpus-selection.md` asks
for: whether a thread found a defect or a naming preference. That decides
whether an entry earns its place, no query expresses it, and a command that
guessed at it would fill the corpus with reviews about whitespace. The threads
come back with the head so a person can read them and decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .entries import parse_pull_url
from .github import GitHub, GitHubError, Thread

# GitHub answers 50 nodes of this shape and refuses 100 with a 502 — the nested
# repository and thread counts are resolved per node, and a hundred of them is
# past what the search endpoint will do in one go.
PAGE_SIZE = 50
# Search never returns more than this, however far the cursor is walked. Hitting
# it means the query was too wide, not that the corpus ran out of candidates.
SEARCH_CEILING = 1000
MAX_PAGES = 20

SEARCH_QUERY = """
query($q: String!, $size: Int!, $cursor: String) {
  search(query: $q, type: ISSUE, first: $size, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        url
        number
        changedFiles
        additions
        deletions
        baseRefOid
        reviewThreads { totalCount }
        repository {
          nameWithOwner
          stargazerCount
          primaryLanguage { name }
          licenseInfo { spdxId }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Candidate:
    """One pull request a search yielded, with the facts the criteria ask about.

    `base` comes back with the search; `head` does not, because deriving it
    costs a request of its own — see `propose_head`.
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
        shape the committed list already uses."""
        return f"{self.slug.split('/')[-1].lower()}-{self.number}"


@dataclass(frozen=True)
class Search:
    """What one query yielded, and whether the ceiling cut it short."""

    candidates: list[Candidate]
    matched: int
    scanned: int

    @property
    def truncated(self) -> bool:
        """Whether GitHub's own ceiling cut the walk short. The fix is a
        narrower query — usually a shorter `created:` window — because no amount
        of paging goes past it."""
        return self.scanned >= SEARCH_CEILING and self.matched > self.scanned

    @property
    def stopped_early(self) -> bool:
        """Whether the page budget ran out while GitHub still had results. Not a
        problem — the caller asked for this — but worth saying, so a thin
        result does not read as a thin corner of GitHub."""
        return not self.truncated and self.matched > self.scanned


def search(
    github: GitHub,
    query: str,
    *,
    min_files: int = 0,
    min_threads: int = 1,
    min_stars: int = 0,
    pages: int = 5,
) -> Search:
    """Pull requests the query yields that pass the filters search cannot express.

    `min_threads` defaults to one: a pull request nobody commented on a line of
    cannot have a review that found anything, so it is never a candidate.
    """
    found: list[Candidate] = []
    cursor: str | None = None
    matched = 0
    scanned = 0
    for _ in range(min(pages, MAX_PAGES)):
        page = _page(github, query, cursor)
        matched = page.get("issueCount", 0)
        nodes = [node for node in page.get("nodes") or [] if node]
        scanned += len(nodes)
        found.extend(
            candidate
            for candidate in (_candidate(node) for node in nodes)
            if candidate.files >= min_files
            and candidate.threads >= min_threads
            and candidate.stars >= min_stars
        )
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
    return Search(candidates=found, matched=matched, scanned=scanned)


def propose_head(github: GitHub, candidate: Candidate) -> tuple[str, list[Thread]]:
    """(the commit the earliest review thread was written against, every thread).

    Not the merged head, and not the base: the state reviewers were looking at
    when they first wrote something about a line. A review that ran over several
    rounds anchors its later threads at later commits — the first one is the
    state before any of the fixes were made.

    Returns an empty head when no thread carries a commit, which is what an
    anonymous REST answer looks like for an old pull request. The threads come
    back either way: they are what the defect-or-preference judgement is made on.
    """
    threads = github.review_threads(parse_pull_url(candidate.url))
    anchored = [thread for thread in threads if thread.commit and thread.comments]
    if not anchored:
        return "", threads
    earliest = min(anchored, key=lambda thread: thread.comments[0].created_at)
    return earliest.commit, threads


def as_toml(candidate: Candidate, head: str) -> str:
    """The `[[entry]]` stanza, with what needs judging left empty.

    `found` and `domain` are blank on purpose. They are the two fields a reader
    uses to decide whether an entry earns its place, and a sentence generated
    from a title would read exactly like one somebody had checked.
    """
    return "\n".join(
        [
            "[[entry]]",
            f'id = "{candidate.id}"',
            f'url = "{candidate.url}"',
            f'base = "{candidate.base}"',
            f'head = "{head}"',
            f'language = "{candidate.language}"',
            'domain = ""    # what this repository is for',
            'found = ""     # the defect the review found, in one line',
            f'license = "{candidate.license}"',
            f"files = {candidate.files}",
            f"added = {candidate.added}",
            f"removed = {candidate.removed}",
            "",
        ]
    )


def _page(github: GitHub, query: str, cursor: str | None) -> dict[str, Any]:
    variables = {"q": query, "size": PAGE_SIZE, "cursor": cursor}
    payload = github.graphql(SEARCH_QUERY, variables)
    result = (payload or {}).get("data", {}).get("search")
    if result is None:
        raise GitHubError(f"search returned no result for {query!r}")
    return result


def _candidate(node: dict[str, Any]) -> Candidate:
    repository = node.get("repository") or {}
    return Candidate(
        url=node.get("url") or "",
        slug=repository.get("nameWithOwner") or "",
        number=int(node.get("number") or 0),
        files=int(node.get("changedFiles") or 0),
        added=int(node.get("additions") or 0),
        removed=int(node.get("deletions") or 0),
        threads=int((node.get("reviewThreads") or {}).get("totalCount") or 0),
        stars=int(repository.get("stargazerCount") or 0),
        language=(repository.get("primaryLanguage") or {}).get("name") or "",
        license=(repository.get("licenseInfo") or {}).get("spdxId") or "",
        base=node.get("baseRefOid") or "",
    )
