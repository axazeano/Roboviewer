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

from dataclasses import dataclass, field
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
# Why a candidate was dropped, in the order the filters ask. The order is the
# useful part: a query that returns nothing usually dies on the first of these,
# and knowing which one names the fix.
NO_REVIEW = "nobody reviewed a line"
TOO_SMALL = "too few files"
TOO_OBSCURE = "too few stars"
LICENCE = "licence not on the allowed list"

# Licences an entry may be recorded under. An allowed list rather than a
# refused one, because the two are not symmetric: what is safe here is a closed
# set that changes about once a decade, while what is unsafe is open and keeps
# growing — BUSL, SSPL, Elastic, FSL, Commons Clause, whatever is written next.
# A refused list is wrong the day after it is written; this one fails towards
# dropping a candidate nobody looked at, which costs nothing.
#
# Copyleft belongs here: every entry is cloned, read locally and never
# redistributed, and copyleft constrains distribution. What does not belong is
# source-available terms, which restrict use itself — the one thing that bites
# without distributing anything.
#
# Note BSL-1.0 is the Boost Software Licence, permissive, and has nothing to do
# with BUSL-1.1, the Business Source Licence, which is source-available. The
# names collide; only one of them is here.
SAFE_LICENCES = frozenset(
    {
        # permissive
        "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "ISC",
        "MIT", "MIT-0", "PostgreSQL", "Python-2.0", "Unlicense", "Zlib",
        # copyleft: constrains distribution, and there is none
        "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
        "CDDL-1.0", "EPL-2.0",
        "GPL-2.0", "GPL-2.0-only", "GPL-2.0-or-later",
        "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
        "LGPL-2.1", "LGPL-2.1-only", "LGPL-2.1-or-later",
        "LGPL-3.0", "LGPL-3.0-only", "LGPL-3.0-or-later",
        "MPL-2.0",
    }
)

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
class Filters:
    """What the search query could not express, applied on this side.

    `min_threads` is one rather than zero: a pull request nobody commented on a
    line of cannot have a review that found anything, so it is never a candidate.

    `licences` keeps what GitHub named as one of `SAFE_LICENCES` and drops
    everything else — including both of GitHub's ways of saying it does not know:
    no `licenseInfo` when it found no file, and `NOASSERTION` when it found one
    it could not map. Neither means the repository is unlicensed; `juju/juju`
    spells the file `LICENCE`, carries AGPL-3.0 in full, and comes back as
    `NOASSERTION`. But an entry records a licence, and a recorded value nobody
    read is a claim nobody checked. Any of it is recoverable by reading the
    repository and writing the licence into the entry by hand.
    """

    min_files: int = 0
    min_threads: int = 1
    min_stars: int = 0
    # None keeps everything, for a candidate somebody has decided to read the
    # licence of by hand.
    licences: frozenset[str] | None = SAFE_LICENCES

    def rejects(self, candidate: Candidate) -> str:
        """Why this candidate is out, or "" when it is in.

        One reason rather than all of them, in a fixed order, so the counts add
        up to the number rejected and can be read as a funnel.
        """
        if candidate.threads < self.min_threads:
            return NO_REVIEW
        if candidate.files < self.min_files:
            return TOO_SMALL
        if candidate.stars < self.min_stars:
            return TOO_OBSCURE
        if self.licences is not None and candidate.license not in self.licences:
            return LICENCE
        return ""


@dataclass(frozen=True)
class Search:
    """What one query yielded, and what was dropped on the way."""

    candidates: list[Candidate]
    matched: int
    scanned: int
    # Why the rest were dropped, one reason each. A sieve that hides what it
    # removed is a sieve nobody can check — and on a broad query the answer is
    # nearly always the same one, which is worth being told rather than guessed.
    rejected: dict[str, int] = field(default_factory=dict)

    @property
    def worst(self) -> tuple[str, int] | None:
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
    rejected: dict[str, int] = {}
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
            if reason:
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
