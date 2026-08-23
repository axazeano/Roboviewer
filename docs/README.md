# Documentation

The [README](../README.md) is the overview and the first run. Everything past
that is here.

## Running it

| Page | What is in it |
| --- | --- |
| [Configuration](configuration.md) | The config file, its two kinds of section, the `--config` rule, checking the gateway, rate limits |
| [Command line](cli.md) | Every flag and the environment variables that stand in for them |
| [Reports and output](reports.md) | What a run writes and where, the four formats, overriding a template |
| [Continuous integration](ci.md) | Exit codes, and a job for GitLab and for GitHub |

## Changing what it reviews

| Page | What is in it |
| --- | --- |
| [How it works](how-it-works.md) | Whole files, the reference pre-pass, one agent per concern, the judge |
| [Customise the checklist](checklists.md) | Adding a concern, and overriding the built-in set from the repository under review |
| [Output language](language.md) | Asking for findings in another language instead of translating the prompts |
| [Tuning](tuning.md) | Prompts, how many agents, thinking, the turn limit |

## Development

| Page | What is in it |
| --- | --- |
| [Architecture](architecture.md) | The map of the code: what each package is for, what depends on what, where to start reading |
| [Measurements](measurements.md) | Recall and false positives per model, checklist size and sampling, with the settings behind each run |
| [Baseline](baseline.md) | Tool counts before and after the simplification work |
| [The benchmark](benchmark.md) | The `benchmark` command: a fixed list of real merge requests, cloned at the reviewed commits and reviewed with one command |
| [Choosing the benchmark](benchmark-selection.md) | Which merge requests belong in it, and what disqualifies one |
| [Watching a run](trace.md) | What the agents did with the context: the log, the page, the command that keeps both |
