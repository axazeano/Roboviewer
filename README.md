# Roboviewer

Automated code review for merge requests, running entirely on your machine.

Point it at two branches and it comes back with a ranked list of problems worth
fixing — not a wall of style nitpicks.

```bash
roboviewer develop
```

```
▸ feature/discount → develop: 12 files, 8 checklist items
• Correctness and logic errors: 3 findings (ok) · 41200 tokens · 62s
• Error handling: 1 findings (ok) · 38900 tokens · 55s
...
▸ Confirmed 4 of 11

  F001  [Blocker] src/cart.py:42 — race when the cart is updated concurrently
  F002  [Major] src/api.py:118 — the change breaks older clients

Confirmed 4 of 11 · 167100 tokens · 67% from cache
Report: .roboviewer/runs/20260730-172900/report.md
```

*Findings come back in English. `--language ru` asks the model for another one
without touching the prompts — see [Output language](#output-language).*

## The problem

**Review arrives late, and tired.** A merge request waits hours or days, and by
the time someone opens it they are on their fourth review of the afternoon. The
blocker ships under three comments about whitespace.

**Hosted reviewers want your code.** CodeRabbit, Copilot and the rest do good
work, but every one of them means uploading the repository to somebody else's
infrastructure. Inside a corporate network that is where the conversation ends.

**They also want your forge.** They plug into GitHub or GitLab and review what is
already a merge request. Looking over your own branch *before* you open it, or
reviewing a mirror that lives nowhere but your laptop, is not something they do.

**Pasting a diff into a chat window invents problems.** Given a few lines of
context, a model will confidently report a missing nil check that sits twenty
lines above the hunk. Nothing is ranked, nothing is verified, and a real blocker
arrives in the same flat list as a naming preference.

## What you get

**Your code stays where it is.** Roboviewer talks to any OpenAI-compatible
endpoint — including a corporate gateway — so reviews never leave the network you
already trust. Nothing is uploaded anywhere else, and there is no service to sign
up for.

**Any git repository, no integration.** It reads two branches through plain git.
No app to install on your organisation, no webhooks, no permissions to request.

**Review before you open the MR.** Run it on your own branch, fix what it finds,
and let the humans spend their attention on design instead of on the bug you
would have caught yourself.

**A ranked list you can act on.** Findings carry a severity, a file and a line,
and a final judge pass throws out the ones that do not survive a second look.

## Requirements

- Python 3.11+
- git
- An OpenAI-compatible endpoint **with tool calling** — the agents drive the
  review through tools, so a completions-only gateway will not work.
  `roboviewer --check-provider` tells you which side of that line yours is on.

## Install

```bash
git clone git@github.com:axazeano/Roboviewer.git && cd Roboviewer
python3 -m venv .venv && .venv/bin/pip install -e .
ln -sf "$PWD/.venv/bin/roboviewer" ~/.local/bin/roboviewer
```

## Configure

```bash
mkdir -p ~/.config/roboviewer
cp config.example.toml ~/.config/roboviewer/config.toml
export ROBOVIEWER_API_KEY=...
```

Set `provider.base_url` and `provider.model`. Everything else has working
defaults and is documented inline in [config.example.toml](config.example.toml).

Then check the gateway actually works:

```bash
roboviewer --check-provider
```

It makes a handful of targeted requests and names what is wrong — wrong auth
scheme, a `base_url` missing `/v1`, a gateway that cannot do tool calling —
instead of leaving you to infer it from eight agents failing at once.

## Use

```bash
roboviewer <target> [source]
```

The target branch is required. The source defaults to your current branch, and
naming it explicitly lets you review someone else's branch without checking it
out.

```bash
roboviewer develop                    # current branch into develop
roboviewer develop feature/login      # someone else's branch
roboviewer -C ~/projects/app develop  # a repository living elsewhere
```

| Flag | What it does |
| --- | --- |
| `-C, --repo` | Repository to review; defaults to `$ROBOVIEWER_REPO` or the current directory |
| `-o, --output` | Where reports go; point it outside the repository to keep `git status` clean |
| `--diff-only` | Show what would be reviewed and stop, without spending tokens |
| `--only correctness,tests` | Run just these checklist items |
| `-v, --verbose` | Stream agent activity: tool calls, retries, errors |
| `--format md,html` | Which reports to write; HTML is one self-contained file |
| `--language ru` | Language the model writes findings in |
| `--check-provider` | Diagnose the gateway and stop |

`ROBOVIEWER_REPO` and `ROBOVIEWER_OUTPUT` cover `-C`/`-o` if you set them once.
`--help` has the rest.

## How it works

Three decisions do most of the work:

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

## Output

`.roboviewer/runs/<timestamp>/` holds `report.md` for humans, plus
`findings.json` and per-item raw results for tuning prompts. `latest` symlinks to
the most recent run. Point `-o` somewhere outside the repository to keep it out
of `git status`.

`--format` picks what a run writes. One file per format in
`roboviewer/renders/`:

| Format | File | For |
| --- | --- | --- |
| `md` | `report.md` | Reading in a terminal, pasting into a merge request |
| `html` | `report.html` | One self-contained file — opens by double click, attaches to a ticket |
| `sarif` | `report.sarif` | SARIF 2.1.0: GitHub Code Scanning, the VS Code viewer, SonarQube |
| `codequality` | `gl-code-quality-report.json` | The Code Quality widget in a GitLab merge request |

```yaml
# .gitlab-ci.yml
review:
  script: roboviewer develop --format md,codequality
  artifacts:
    reports:
      codequality: .roboviewer/runs/latest/gl-code-quality-report.json
```

`md` and `html` are Jinja templates in `roboviewer/templates/default/`; drop a
changed copy into `.roboviewer/templates/` to override one file by file, or add
a new `report.<name>.j2` and `--format <name>` picks it up without touching
Python. `sarif` and `codequality` are serialization rather than documents, so
they are plain Python and never go near a template.

## Customise the checklist

One markdown file per concern, in `roboviewer/checklists/default/`:

```markdown
---
id: correctness
title: Correctness and logic errors
order: 10
---
The task for the agent...
```

Adding a check means adding a file — no code involved. A `checklists/` directory
inside the repository being reviewed overrides the built-in set. An optional
`_system.md` in the directory replaces the system prompt for its items.

## Output language

Prompts and checklists are English, so findings come back in English. To get
them in another language, ask for it rather than translating the prompts:

```bash
roboviewer develop --language ru
```

Or once, in the config:

```toml
[run]
output_language = "ru"
```

It takes an ISO code or a name — `ru`, `Russian`, `German`. Anything not in the
built-in map goes into the prompt as written, so `Bahasa Indonesia` works too.

This covers the text the model writes: finding titles, rationales, suggestions,
verdict reasons and the judge's summary. Report headings, tables and severity
labels come from the templates and stay English — change those in
`roboviewer/templates/default/`.

The directive is appended by the code, so it survives a custom prompt set and a
checklist that brings its own `_system.md`.

## Tuning

The four texts the agents actually run on — the reviewer's system prompt and
task, the judge's — are markdown files in `roboviewer/prompts/default/`. Drop a
changed copy of any of them into `.roboviewer/prompts/` inside the repository
being reviewed and it wins; the rest keep coming from the bundled set.
`--show-config` prints which file came from where, and a typo in a placeholder
fails the run before the first request instead of eight agents deep.

Three checklist sets ship with the tool, differing only in how the aspects are
distributed between agents — the aspect texts themselves are identical, so
running the same MR through each compares structure rather than wording:

```bash
roboviewer develop                                 # default: 8 aspects, one agent each
roboviewer develop --checklist checklists/grouped  # 3 agents over related aspects
roboviewer develop --checklist checklists/single   # one agent for everything
```

Fewer agents means the context block is resent fewer times, which is most of the
token bill — at the cost of each agent holding more objectives at once. Smaller
models tend to lose the later aspects when asked to hold many. Compare the
`report.md` tables to see how the trade lands on your model.

A slow run is rarely slow for the reason it looks like. Resending the same
context block to eight agents is the visible cost and usually not the real one:
providers serve a repeated prefix from cache. The time goes into the model
thinking, token by token, on every turn. `--thinking off` runs a reasoning model
with that switched off — a large speedup, and a large risk to depth, so it
belongs on a merge request whose problems are already known.

Watch the status column for ⚠️. On its last turn an agent is forced to submit
whatever it has, so an aspect that ran out of `max_turns` hands back a thin
result that reads exactly like a clean pass. The report calls those out under
*Cut off by the turn limit*, together with whatever conclusion each one reached.

Read that conclusion before raising `max_turns`. An agent is told its budget and
asked to land before it runs out, but if it still gets cut off while its summary
already reads as finished, it was not short of turns — it never stopped, and a
bigger budget buys nothing. Measured on a 64-file MR: 15 → 25 turns left the
same seven of eight agents cut off and cost 67% more tokens.

Beyond that, `--model` swaps the model for a single run, `-j` changes how many
items run concurrently, `--judge-mode two-stage` gives every finding its own
verification pass and then a judge over the survivors, and `--no-judge` skips
verification entirely — useful when you are iterating on a prompt and want the
raw output.

## What it doesn't do

- It does not post comments on your merge request, and does not talk to GitHub or
  GitLab at all. Output is files on disk.
- It does not modify your code. The agents get read-only tools —
  `read_file`, `grep`, `list_files`, `git_show` — and nothing else.
- It does not replace a human reviewer. It catches the class of problem that
  survives a tired read; it has no idea whether the feature was worth building.

## License

MIT — see [LICENSE](LICENSE).
