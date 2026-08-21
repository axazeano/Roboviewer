"""The `[[entry]]` table of the corpus list, written from a candidate.

One direction of a mapping whose other direction is `entries.py`: that parses
this text into an `Entry`, this writes what it will parse. The round-trip test
is what keeps the two from drifting.

Text rather than an `Entry` dumped back out, though the shape is the same one.
The head is empty whenever no review thread named the commit it was written
against, and the model refuses anything short of a full SHA — so what is printed
here is exactly what it will not hold yet.
"""

from __future__ import annotations

from .criteria import Candidate


def from_candidate(candidate: Candidate, head: str) -> str:
    """The table, with what needs judging left empty.

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
