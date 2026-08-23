"""The `[[entry]]` table of the index, drafted from a candidate.

The same table `items.render` writes for `list add`, with one difference: the
head is empty whenever no review thread named the commit it was written
against, and `Entry` refuses anything short of a full SHA — so the draft is
built without validation and printed for a person to finish.
"""

from __future__ import annotations

from .. import items
from .candidate import Candidate


def from_candidate(candidate: Candidate, head: str) -> str:
    """The table, with what needs judging left empty."""
    return items.render(
        items.Entry.model_construct(
            id=candidate.id,
            url=candidate.url,
            base=candidate.base,
            head=head,
            language=candidate.language,
            domain="",
            found="",
            license=candidate.license,
            files=candidate.files,
            added=candidate.added,
            removed=candidate.removed,
        )
    )
