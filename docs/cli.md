# Command line

Every command the tool takes, the flags around them, and the environment
variables that stand in for the ones you would otherwise retype.

```bash
roboviewer review --from <branch> --into <branch>
```

Both branches are named rather than ordered — `--into` is what the branch merges
into. `--into` may be left out inside a merge-request pipeline, which names the
target in its environment; `--from` defaults to your current branch, and naming
it lets you review someone else's without checking it out.

```bash
roboviewer review --into develop                        # current branch into develop
roboviewer review --from feature/login --into develop   # someone else's branch
roboviewer review --into develop --repo ~/projects/app  # a repository living elsewhere
```

## The commands

| Command | What it does |
| --- | --- |
| `init` | Set the tool up by answering questions, and write the two config files |
| `review` | Compare the branches, run the checklist, write the reports |
| `diff` | Print what would be reviewed and stop, before a token is spent |
| `list-items` | Print the checklist items this run would use |
| `show-config` | Print the settings a run would use, and the file they came from |
| `check-provider` | Probe the gateway and break the answer down, for debugging 401 and friends |

`review` and `diff` compare two branches and need a repository; the other four
work on the setup and run anywhere. `init` is the one to run first — it writes
the two files every other command reads.

```bash
roboviewer init
```

It asks for the gateway's address and how it wants the key, the model, whether
the judge gets a stronger one, the language findings are written in, which
reports to write, whether a CI job should go red, and which stacks' generated
files to keep out of a review. Every question has a default, and each file it
writes is the annotated example with the answers set into it.

Lists are answered with the arrows: `↑`/`↓` move, `space` marks an entry where
several are allowed, `Enter` takes what is under the cursor, and `Escape` stops
the interview. Without a terminal to draw on — a pipe, `ssh -T`, a `TERM` that
cannot move a cursor, Windows — the same lists are numbered and the number is
typed instead. An existing file
is never overwritten without being asked, the key stays in an environment
variable unless you ask for it to be written down (mode `600` when you do), and
at the end it offers to run `check-provider` against what it just wrote. It is a
conversation, so it refuses a stdin that cannot hold one rather than hanging in
a pipeline.

## The flags

| Flag | Commands | What it does |
| --- | --- | --- |
| `--from BRANCH` | `review`, `diff` | Source branch — what is merged; defaults to the current one |
| `--into BRANCH` | `review`, `diff` | Target branch — what it is merged into |
| `--repo PATH` | all but `init`, `check-provider` | Repository to review; defaults to `$ROBOVIEWER_REPO` or the current directory |
| `--config PATH` | all but `init` | Read these settings instead of `~/.config/roboviewer/config.toml`; the provider still comes from `provider.toml`, and a `[provider]` section here is refused |
| `--checklist DIR` | `review`, `list-items` | Directory holding the checklist items |
| `--only correctness,tests` | `review`, `list-items` | Run just these checklist items |
| `-o, --output PATH` | `review` | Where reports go; point it outside the repository to keep `git status` clean |
| `--format md,html` | `review` | Which reports to write; HTML is one self-contained file |
| `--language ru` | `review` | Language the model writes findings in |
| `--fail-on major` | `review` | Exit 1 on a confirmed finding this bad or worse, so CI goes red |
| `-j, --concurrency N` | `review` | How many items to review in parallel |
| `--no-judge` | `review` | Skip the final judge pass |
| `-v, --verbose` | `review` | Stream agent activity: tool calls, retries, errors |

`ROBOVIEWER_REPO` and `ROBOVIEWER_OUTPUT` cover `--repo`/`-o` if you set them
once, and `ROBOVIEWER_PROVIDER_CONFIG` names the provider file where there is no
`~/.config/roboviewer/` — a runner, a container. `--help`, and `<command>
--help`, have the rest.

Most of what fits a tool to a model is a config setting rather than a flag, and
[Tuning](tuning.md) explains which way round and why.
