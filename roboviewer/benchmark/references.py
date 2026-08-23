"""What a good review of an entry finds: `references/<id>.toml`.

One file per entry, written by a person who checked every claim against the
code at the entry's head. Each `[[finding]]` is either a defect the review has
to find (`verdict = "expected"`) or a claim the review once produced that was
checked and proven wrong (`verdict = "false"`); both are needed, because
without the false ones high recall can be bought with noise.

Nothing here scores a run — that is TASK-20 — but the file has a shape, and a
loader that refuses a malformed one keeps the shape from drifting while the
scorer does not exist yet. Extra keys are refused for the same reason they are
in `items`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from ..config import STRICT

Verdict = Literal["expected", "false"]
Origin = Literal["manual", "verified-from-run"]


class ReferenceFinding(BaseModel):
    """One checked claim about the merge request."""

    model_config = STRICT

    id: str
    verdict: Verdict
    origin: Origin
    severity: str
    file: str
    what: str
    # What the claim was checked with — a command, a line range, a type check.
    evidence: str
    kind: str = ""
    line: int | None = None
    # Why a claim with verdict "false" is false. Required there, absent otherwise.
    why_false: str = ""

    @model_validator(mode="after")
    def _false_says_why(self) -> ReferenceFinding:
        if self.verdict == "false" and not self.why_false:
            raise ValueError(f"{self.id}: a false claim has to say why (why_false)")
        return self


class ReferenceMeta(BaseModel):
    """The pull request, and when the reference was established. The commits,
    the size and the language live in `items.toml` and are not repeated."""

    model_config = STRICT

    url: str
    established: str = ""
    extended: str = ""
    extended_2: str = ""
    note: str = ""


class Reference(BaseModel):
    model_config = STRICT

    meta: ReferenceMeta
    finding: list[ReferenceFinding] = Field(default_factory=list)

    @property
    def expected(self) -> list[ReferenceFinding]:
        return [f for f in self.finding if f.verdict == "expected"]

    @property
    def false(self) -> list[ReferenceFinding]:
        return [f for f in self.finding if f.verdict == "false"]

    @model_validator(mode="after")
    def _check_unique_ids(self) -> Reference:
        seen: set[str] = set()
        for finding in self.finding:
            if finding.id in seen:
                raise ValueError(f"two findings share the id {finding.id!r}")
            seen.add(finding.id)
        return self


def load_reference(path: Path) -> Reference:
    """The reference in the file, or a message saying what is wrong with it."""
    resolved = path.expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"Reference not found: {resolved}")
    with resolved.open("rb") as fh:
        raw = tomllib.load(fh)
    try:
        return Reference.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{resolved}:\n{exc}") from exc
