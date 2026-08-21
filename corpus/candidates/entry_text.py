"""The text of an `[[entry]]`, half-written, for a person to finish.

Text rather than an `Entry`, and there is no draft type here, because a draft
is precisely what the model refuses to be: the head is empty whenever no review
thread named the commit it was written against, and `Entry` wants a 40-character
SHA. A type for that would be a type for a state the corpus does not accept.

So this writes the shape `entries.py` reads, and the round-trip test is what
keeps the two from drifting. It changes when the entry format does, and not when
the search moves to another forge.
"""

from __future__ import annotations

from .criteria import Candidate


def as_toml(candidate: Candidate, head: str) -> str:
    """The `[[entry]]` block, with what needs judging left empty.

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
