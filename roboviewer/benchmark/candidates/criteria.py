"""The bar a candidate is held to, and what to say when it does not clear it.

Nothing here talks to GitHub, and nothing here is a candidate: `Filters` is the
bar, `Reason` is the answer when it is missed. Which makes this the one file to
read to find out what the command keeps, and the one to change to keep something
else.

The prose version is `docs/benchmark-selection.md`, and most of it stays prose on
purpose: whether a review found a defect or argued about naming is judgement,
and this is only the part of the bar a machine can apply.
"""

from __future__ import annotations

from dataclasses import dataclass

from .candidate import Candidate


@dataclass(frozen=True)
class Reason:
    """Why a candidate was dropped, and what to do about it.

    The fix travels with the reason because the two are only useful together. A
    funnel that says "171 dropped — too few files" and leaves the reader to work
    out which flag to lower has said half of what it knows, and a run that found
    nothing usually died on exactly one of these.
    """

    why: str
    fix: str


# In the order the filters ask. The order is the useful part: a query that
# returns nothing usually dies on the first of these, and knowing which one
# names the fix.
NO_REVIEW = Reason(
    "nobody reviewed a line",
    "Most merged pull requests are bots and solo merges. Add "
    "review:changes_requested to the query — on a broad search it takes "
    "the reviewed share from about 2% to about 80%.",
)
TOO_SMALL = Reason(
    "too few files",
    "Lower --min-files, or search a repository whose changes run larger.",
)
TOO_OBSCURE = Reason("too few stars", "Lower --min-stars.")
LICENCE = Reason(
    "licence not on the allowed list",
    "Read the repository and use --any-license if one of them is worth it.",
)

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

    def rejects(self, candidate: Candidate) -> Reason | None:
        """Why this candidate is out, or None when it is in.

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
        return None
