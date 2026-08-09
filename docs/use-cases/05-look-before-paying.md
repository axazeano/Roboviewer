# See what would be reviewed without paying for it

A review costs tokens and minutes. Before spending them, someone wants to know
which files are in, which are out, and what the agents will be asked.

## What serves it

| Surface | Part |
| --- | --- |
| `--diff-only` | print the diff summary and stop |
| `--list-items` | print the checklist items and stop |
| `run.exclude_globs` | what is kept out of the review entirely |

## What it costs to learn

Two flags that stop early, and one setting that explains a surprise. The
natural sequence is `--diff-only`, notice a file that should not be there, add
a glob, run it again — and that loop costs nothing, which is the point.

`exclude_globs` has one sharp edge: setting it in a config **replaces** the
defaults rather than extending them, so a person who excludes one directory
silently loses the lockfile and vendored-code exclusions. The comment in
`config.py` says so and `config.example.toml` says so, but the behaviour is
discovered rather than announced — the run just gets bigger.

## Verdict: keep

Both flags earn their place: they are the cheap half of an expensive tool, and
`--diff-only` is the first thing to reach for when a run costs more than
expected.

The replace-versus-extend edge on `exclude_globs` is worth a follow-up, but it
is a defaulting question rather than surface — the fix is either to merge with
the defaults and offer a way to clear them, or to say what was dropped when a
config replaces them. Filed as a draft, since either answer changes behaviour.
