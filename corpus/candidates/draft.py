"""The `[[entry]]` a candidate becomes, half-written, for somebody to finish.

A draft rather than an entry: the two fields carrying judgement are left
blank, and a person fills them in or throws the whole thing away.

The one part of this package that survives moving the search to another
forge, and the one that changes when the entry format does: `entries.py`
reads this shape, and this writes it.
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
