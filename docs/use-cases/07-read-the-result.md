# Read the result

The run has finished. Someone wants to read the findings — in the terminal, in
a file they can paste into a merge request, or in whatever their forge renders.

## What serves it

| Surface | Part |
| --- | --- |
| `--format LIST`, `run.report_formats` | which reports get written |
| `run.templates_dir` | a changed copy of a report template |
| `run.output_dir`, `-o` | where they land, plus the `latest` symlink |
| `-v, --verbose` | tool calls, retries and errors while it runs |

## What it costs to learn

Four format names and one flag. `md` for reading, `html` for attaching to a
ticket, `sarif` and `codequality` for a forge. Each is one file in
`renders/`, so the directory listing is the list of formats, and an unknown
name fails on a message that names the known ones.

`--format` replaces the configured list rather than extending it, which is the
right way round: `--format md` means "just markdown this time", and appending
would make that impossible to say. Unlike `exclude_globs`, this one is
announced in the help text.

`-v` belongs here rather than with tuning: what it answers is "is it stuck", a
question anyone asks on their first run against a slow gateway.

## Verdict: keep

The custom-template path — drop `report.<name>.j2` into the templates directory
and `--format <name>` picks it up — is more surface than most people need, but
it is what makes a format addable without touching Python, and it costs nothing
to ignore. See case 9 for the directory it lives in, which is the part that
costs.
