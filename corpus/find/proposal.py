"""From a candidate that survived the sieve to an entry somebody can check.

Both halves are proposals rather than answers, and neither is safe to accept
unread. The head is derived from where the review was written, which is right
whenever the first thread is about a defect and wrong when a first round of
naming notes came before it. The stanza leaves blank exactly the two fields
that carry judgement.
"""

from __future__ import annotations

from ..entries import parse_pull_url
from ..github import GitHub, Thread
from .criteria import Candidate


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
