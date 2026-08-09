# Review my branch before opening the merge request

The reason the tool exists. Someone has finished a branch, wants the mechanical
problems found before a human spends attention on it, and does not want to
upload the repository anywhere.

## What serves it

| Surface | Part |
| --- | --- |
| `target` (positional) | what we merge into; the only required argument |
| `source` (positional) | defaults to the current branch |
| `-C, --repo`, `$ROBOVIEWER_REPO` | which repository |
| `-o, --output`, `$ROBOVIEWER_OUTPUT` | where reports go |
| `run.output_dir` | the same, from the config |

## What it costs to learn

`roboviewer develop`. One word after the command, and everything else has a
default: the source is the branch you are on, the repository is where you
stand, the output goes under `.roboviewer/runs`.

The comparison is against the merge base rather than the branch tip, so commits
that landed on the target after you forked stay out. Nobody has to know that to
use it, and everybody would have to know it if it were the other way round.

`-o` exists for one concrete annoyance — reports landing inside the working
tree and showing up in `git status` — and both it and `-C` have environment
variables so the annoyance is settled once rather than per invocation.

## Verdict: keep

The whole surface here is two positionals with sensible defaults and two paths.
Nothing to fold: the environment variables are not a third way of doing it,
they are the same setting held still.
