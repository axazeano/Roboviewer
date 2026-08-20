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
without touching the prompts — see [Output language](docs/language.md).*

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

## Where it stands

Measured on [nextcloud/ios#4091](https://github.com/nextcloud/ios/pull/4091) —
an Albums feature, 32 files and 3903 lines once the resource files are excluded —
against 31 defects established by hand. This is a starting line, published so it
can be argued with and moved; the full table, every setting and the caveats are
in [Measurements](docs/measurements.md).

**Recall.** How many of the 31 known defects a configuration surfaced across its
runs. One block is one defect.

```
                                  ┌───────────────────────────────┐ 31
nemotron-lightning-30b · 8 items  │██                             │  2   6%
muse-glimmer-30b · 3 items        │████████████                   │ 12  39%
muse-glimmer-30b · 8 items        │███████████████                │ 15  48%
claude-opus-5 · 1 item            │██████████████████████         │ 22  71%
                                  └───────────────────────────────┘
```

**False positives.** Of everything a configuration shipped, how much was
verified wrong against the code. Solid is proven false; light is not yet
adjudicated, so the real rate sits inside the bar.

```
                                  0%        10%       20%       30%
                                  ├─────────┼─────────┼─────────┤
nemotron-lightning-30b · 8 items  │██░░░░░░                     │   4%…15%
muse-glimmer-30b · 3 items        │██████░░░░                   │  12%…20%
muse-glimmer-30b · 8 items        │████░░░░░░░░░░░              │   8%…30%
claude-opus-5 · 1 item            │█░░░░░░░░░░                  │   2%…21%
```

A low rate can still be a bad report. Of the 48 findings the nemotron
configuration shipped, 36 are one "this file has no tests" entry per file —
formally correct, and one thought repeated thirty-six times. Counting findings
rewards that; counting distinct defects does not, which is why the recall chart
above is drawn per defect rather than per finding.

Three things this does not say. It is one merge request, so `n = 1` at the
repository level. Fifteen of the 31 defects entered the truth set by verifying
one model's output, so that model is being graded partly on ground it defined —
`truth.toml` marks which entries are independent. And Opus ran without the judge,
as a ceiling to aim at rather than a configuration of this tool.

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

### Docker

The image carries the tool and git. The repository under review, the config and
the reports stay on your side, as mounts.

```bash
docker run --rm \
  -v "$PWD:/repo" \
  -v ~/.config/roboviewer/config.toml:/config.toml:ro \
  -v "$PWD/.roboviewer:/out" \
  -e ROBOVIEWER_API_KEY \
  axazeano/roboviewer:0.1.2 develop --config /config.toml --output /out
```

Mount the repository with its history — a shallow clone has no merge base to
diff against. The image runs as an unprivileged user; on Linux add
`--user "$(id -u):$(id -g)"` so the reports it writes belong to you.

## Configure

```bash
mkdir -p ~/.config/roboviewer
cp config.example.toml ~/.config/roboviewer/config.toml
export ROBOVIEWER_API_KEY=...
roboviewer --check-provider
```

Set `provider.base_url` and `reviewer.model`. Everything else has working
defaults and is documented inline in [config.example.toml](config.example.toml).
`--check-provider` makes a handful of targeted requests and names what is wrong
— wrong auth scheme, a `base_url` missing `/v1`, a gateway that cannot do tool
calling — instead of leaving you to infer it from eight agents failing at once.

The sections, the rule that `--config` replaces rather than layers, and what to
do about rate limits: [Configuration](docs/configuration.md).

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

Reports land in `.roboviewer/runs/<timestamp>/`, and `--diff-only` shows what
would be reviewed without spending tokens. Every flag:
[Command line](docs/cli.md).

## Documentation

| Page | What is in it |
| --- | --- |
| [Configuration](docs/configuration.md) | The config file, checking the gateway, rate limits |
| [Command line](docs/cli.md) | Every flag and the environment variables behind them |
| [How it works](docs/how-it-works.md) | Whole files, the reference pre-pass, one agent per concern, the judge |
| [Reports and output](docs/reports.md) | What a run writes, the four formats, overriding a template |
| [Continuous integration](docs/ci.md) | Exit codes, and a job for GitLab and for GitHub |
| [Customise the checklist](docs/checklists.md) | Adding a concern without touching code |
| [Output language](docs/language.md) | Findings in a language other than English |
| [Tuning](docs/tuning.md) | Prompts, how many agents, thinking, the turn limit |
| [Measurements](docs/measurements.md) | What it finds and gets wrong, per model and checklist size |

[docs/](docs/) also carries the tooling baseline and how the measurement corpus
is built.

## What it doesn't do

- It does not post comments on your merge request, and does not talk to GitHub
  or GitLab at all. Output is files on disk; in CI it is the pipeline that
  publishes them, from formats the forge already understands.
- It does not modify your code. The agents get read-only tools —
  `read_file`, `grep`, `list_files`, `git_show` — and nothing else.
- It does not replace a human reviewer. It catches the class of problem that
  survives a tired read; it has no idea whether the feature was worth building.

## License

MIT — see [LICENSE](LICENSE).
