# How it works

What a run does between the two branch names and the ranked list, and the four
decisions that shape it.

**Whole files, not hunks.** The agent gets each changed file in full, with changed
lines marked up, and falls back to hunks only for files past
`inline_max_lines` — where it can still pull the rest in with `read_file`.

**A reference check before any agent runs.** Whether a reference resolves is
decidable, so it is settled by searching the tree rather than by asking a model:
identifiers with no declaration, storyboards named but never added, localization
keys with no entry, outlets no nib connects, source files no build manifest
mentions. It reads the resource files excluded from the review context — the
right thing to search, the wrong thing to show a model — and the result goes
into the shared prompt prefix, so it costs one lookup for the whole run.

**What a build decides is not reviewed.** A reviewer is forbidden to check
whether the code compiles, and told to break off the moment it starts: no
grepping a declaration to see whether a call matches it, no chasing a symbol
that looks undefined. The compiler reports all of that first and is never wrong
about it, while a reviewer making the same claim is wrong often enough to have
shipped false blockers. So the two halves of the reference check are used
differently — the symbol census is context, keeping an unfamiliar name from
sending an agent down the wrong path, while the resource misses are findings:
a storyboard scene, a build manifest, a localization key and an asset name are
checked by no compiler at all, and the code holding them builds cleanly and
fails when the screen opens.

**One agent per concern.** Eight specialised reviewers run in parallel — one for
correctness, one for concurrency, one for tests — rather than one generalist
holding everything at once. Each gets read-only tools to dig through the rest of
the repository on its own.

**A judge at the end.** Every finding is re-checked against the code by a final
agent that discards false positives and downgrades inflated severities. Rejecting
a third of them is a normal outcome.

By default that is one pass over the whole list, which is cheap and catches
duplicates naturally. `judge_mode = "two_stage"` spends a separate pass on each
finding first: the turn budget goes to one claim rather than being split across
all of them, a pass that dies costs one verdict instead of every verdict, and
each pass gets a one-line roster of the other findings so duplicates remain
findable.

Those passes settle facts, not proportion — and the second stage is what makes
them usable. Verification and calibration are different jobs: a pass holding one
claim can settle whether it is true, but severity is comparative and it has
nothing to compare against, so severities drift up and the same complaint
confirmed once per file arrives five times. The second pass sees the survivors
together — with the note each verification wrote, so it reads the check instead
of repeating it — recalibrates severity, collapses the repetition and writes the
verdict on the merge request. It costs N + 1 passes.

Comparison is against the **merge base**, so commits that landed on the target
branch after you forked stay out of the review.
