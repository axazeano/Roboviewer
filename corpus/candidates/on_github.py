"""Finding candidates on GitHub: everything only GitHub can answer.

Two questions, and they are together because one thing replaces both — another
forge, whose search and whose reviews would have to be asked differently. What
a candidate has to be is `criteria.py` and stays true whoever is asked.

Search is the only part of the corpus commands that must be GraphQL: the size
of a pull request is not a search qualifier, but it comes back as a field, so
asking for it with the results is what makes filtering here affordable.

Both search limits are GitHub's rather than ours, and both look like a thin
result from the outside — which is why `Search` tells them apart. A page is
fifty because a hundred nodes of this shape times out with a 502, and no query
returns more than a thousand results however far the cursor is walked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..entries import parse_pull_url
from ..github import GitHub, GitHubError, Thread
from .criteria import Candidate, Filters, Reason

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
class Search:
    """What one query yielded, and what was dropped on the way."""

    candidates: list[Candidate]
    matched: int
    scanned: int
    # Why the rest were dropped, one reason each. A sieve that hides what it
    # removed is a sieve nobody can check — and on a broad query the answer is
    # nearly always the same one, which is worth being told rather than guessed.
    rejected: dict[Reason, int] = field(default_factory=dict)

    @property
    def worst(self) -> tuple[Reason, int] | None:
        """The reason that took the most, for a run that found nothing."""
        if not self.rejected:
            return None
        return max(self.rejected.items(), key=lambda pair: pair[1])

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
    filters: Filters | None = None,
    pages: int = 5,
) -> Search:
    """Pull requests the query yields that pass the filters search cannot express."""
    filters = filters or Filters()
    found: list[Candidate] = []
    rejected: dict[Reason, int] = {}
    cursor: str | None = None
    matched = 0
    scanned = 0
    for _ in range(min(pages, MAX_PAGES)):
        page = _page(github, query, cursor)
        matched = page.get("issueCount", 0)
        nodes = [node for node in page.get("nodes") or [] if node]
        scanned += len(nodes)
        for candidate in (_candidate(node) for node in nodes):
            reason = filters.rejects(candidate)
            if reason is not None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            found.append(candidate)
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
    return Search(candidates=found, matched=matched, scanned=scanned, rejected=rejected)


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
