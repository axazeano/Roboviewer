# Changelog

What changed between releases, newest first.

A release is a `Release/<version>` tag on `main`. The tag runs the suite,
refuses to publish when its number and the version in `pyproject.toml`
disagree, and then puts the wheel on [PyPI][pypi] and the image on
[Docker Hub][docker]. Versions follow [semantic versioning][semver].

[pypi]: https://pypi.org/project/roboviewer/
[docker]: https://hub.docker.com/r/axazeano/roboviewer
[semver]: https://semver.org

## Unreleased

The command line says what it does: a command per job and the branches by name,
and the first of those commands sets the tool up by asking. The code is laid out
by subsystem, so that a person opening any file knows where they are, and the
corpus becomes a benchmark with a command of its own. Nothing a review does,
writes or prints changed.

- **`roboviewer init`, the setup asked rather than copied.** A short interview —
  the gateway's address and how it wants the key, the model, a stronger judge or
  not, the language, the reports, the CI gate, the stacks whose generated files
  a review should not read — writes `~/.config/roboviewer/provider.toml` and
  `config.toml`. Every question has a default, so Enter throughout leaves a
  working setup. What lands is the annotated example with the answers set into
  it, comments and all, rather than a generated file holding only the answers.
  An existing file is never overwritten without being asked, the key stays in an
  environment variable unless writing it down is chosen explicitly (mode `600`
  when it is), and the last question offers the `check-provider` probe against
  what was just written. A stdin that cannot answer is refused with exit 2
  instead of hanging. The two example files moved into the package, at
  `roboviewer/config/examples/`, since the wizard writes them and they now ship
  in the wheel.
- **A command per job, and the branches named — breaking.** The review is
  `roboviewer review --from <branch> --into <branch>`: two bare branch names in
  a fixed order could not be read back off a shell history without remembering
  which way round they went. The repository is `--repo PATH`, git's `-C` is
  gone, and the four things that were never a review have names of their own —
  `roboviewer diff`, `list-items`, `show-config`, `check-provider` — instead of
  the flags `--diff-only`, `--list-items`, `--show-config` and
  `--check-provider`. Defaults are unchanged: `--from` is the current branch,
  `--into` falls back to the branch a merge-request pipeline names, so a CI job
  still carries no branch at all. The old positional form is refused with exit
  2 and a line naming the new one. Everything else — the other flags, the
  config keys, the report names, the four exit codes — is untouched.
- **`benchmark`, a second console script**, shaped like git: `benchmark list
  add <pull-request>` records a merge request in `benchmarks/items.toml` and
  clones it at the commit reviewers saw, `list show` and `list remove` keep the
  index, `benchmark run [roboviewer flags]` reviews every entry with the tool —
  one run per entry, the flags passed through unchanged — and writes a
  `summary.json` per run, `fetch` clones what is listed, `search` finds
  candidates on GitHub. The package is `roboviewer.benchmark`, the data lives
  under `benchmarks/` at the repository root: the index and the references
  committed, the clones, comments and runs ignored.
- **`benchmark run --repeats N`** reviews every entry N times, and the summary
  reports statistics over the repeats — one table per repository and one for
  the whole run: runner and judge prompt, completion and cached token totals,
  mean and percentiles of tokens and time per review, and the self-consistency
  of the repeats, overall and per severity, as mean pairwise Jaccard over the
  confirmed findings keyed by file and line.
- **`benchmark run --parallel N`** reviews N entries at once — repeats of one
  entry stay sequential, each worker's output lines carry its entry id, the
  summary keeps updating after every finished review, and a stop lets running
  reviews finish without starting new entries. A benchmark bounded by the
  model's generation speed finishes close to N times sooner.
- **Reports land under the run, not inside the clone.** With the default
  relative benchmarks root, `benchmark run` handed the tool a relative output
  path, which the tool resolves against the repository under review — the
  entry's clone. The path is passed absolute now, so reports go to
  `runs/<stamp>/<id>/<run_id>/` as documented instead of vanishing with the
  cache.
- **The summary survives an interruption**: `summary.json` and `summary.md`
  are rewritten after every finished review rather than once at the end, so a
  benchmark run killed halfway keeps its tables over what it completed, and a
  running one can be watched by opening the page.
- **The references**: `measure/truth.toml` is now
  `benchmarks/references/ios-4091.toml`, one `[[finding]]` per checked claim
  with a `verdict` — `expected` for a defect the review has to find, `false` for
  a claim that was checked and refuted — and the merge request it describes is
  an entry of the index.

- **One package per subsystem** inside `roboviewer/`: `config`, `repo` (all of
  git), `provider` (the model's gateway), `review` (the pipeline and everything
  the model reads, under `review/prompts/`), `reports`, `cli`; the shared
  vocabulary in `models.py` and the one observer protocol in `observer.py` at
  the root. [docs/architecture.md](docs/architecture.md) is the map.
- **The run tracer lives under `measure/`**: `measure.trace`, formerly
  `research`, watches a run (`python -m measure.trace review --into develop`), outside
  the wheel. `measure.corpus` existed briefly on the way to `benchmark`.
- Import paths changed throughout; nothing outside the repository imported
  them. Apart from the command line above, the config files, the report formats
  and the prompt texts are exactly what 0.2.0 shipped.

## 0.2.0 — 2026-08-20

The provider moves into a file of its own, so the file you copy cannot carry a
key. Breaking for one path: a provider passed through `--config`.

- **`[provider]` now lives in `~/.config/roboviewer/provider.toml`** and is read
  from there on every run. A `--config` file carrying a `[provider]` section is
  refused, with an error naming where the provider belongs. The two halves of
  the old file had opposite lifetimes: the gateway is set once per machine,
  while the reviewer and run settings get copied constantly — into experiments,
  into write-ups that record how a run was configured — and every copy took the
  key along. The file people pass around can no longer hold a secret, not by
  convention but because such a file will not start a run.
- **Nobody's setup breaks on upgrade.** A `config.toml` that still holds a
  `[provider]` section keeps working, and every run says how to split it. Where
  both files carry a provider, `provider.toml` wins and the leftover section is
  called out rather than silently ignored.
- **`ROBOVIEWER_PROVIDER_CONFIG` names the provider file** where there is no
  `~/.config/roboviewer/` — a CI runner, a container. Without it the split would
  have locked pipelines out: the jobs in the documentation passed everything
  through `--config`.
- **The key is no longer printed in any form.** `--check-provider` and
  `--show-config` used to show the first four and last four characters. That is
  eight characters of live key material in terminal scrollback, in screenshots
  and in whatever CI keeps of a job's log. What is printed now is which file or
  variable the key came from, which is what a 401 needs.
- **`config.example.toml` is split** into it and `provider.example.toml`.

## 0.1.2 — 2026-08-17

Distribution only: the Python package is what 0.1.1 shipped, with a new number
on it.

- **The image moved to Alpine.** On Debian the git package pulls in perl, and
  perl held both 9.1 CVEs of the 76 Docker Scout reported for
  `python:3.11-slim` — a base that was already current, so no rebuild was going
  to clear them. The Alpine build reports no critical, high, medium or low
  vulnerability at all, and comes to 172 MB across 90 packages against 426 MB
  across 212. pip, setuptools and wheel are dropped after the install: nothing
  at runtime reads them, and they were the only two fixable highs on any base.
  The `docker run` line from the README works unchanged.

## 0.1.1 — 2026-08-17

The tool itself did not change.

- **An image beside the wheel.** `axazeano/roboviewer` for linux/amd64 and
  linux/arm64, carrying the tool and git and nothing else: the repository under
  review, the config and the reports are mounts. It runs as an unprivileged
  user, reads the key from `ROBOVIEWER_API_KEY`, and bakes in no config file. A
  `Release/*` tag now pushes `<version>` and `latest` alongside the wheel.

## 0.1.0 — 2026-08-17

First release. Automated review of a merge request that runs on your machine
and talks to no one but the OpenAI-compatible endpoint you point it at.

- **Two branches, read through plain git.** `roboviewer <target> [source]`
  diffs the branches, hands each changed file to the agents in full with the
  changed lines marked up, and needs no app installed on a forge and no
  webhook. The repository can be one that lives nowhere but your laptop.
- **One agent per concern, then a judge.** Eight specialised reviewers run in
  parallel — correctness, error handling, concurrency, API contracts, security,
  performance, tests, architecture — each with read-only tools (`read_file`,
  `grep`, `list_files`, `git_show`) to dig through the rest of the tree. A
  final pass re-checks every finding against the code and throws out the ones
  that do not survive it. `--checklist` swaps the set for `grouped`, `single`
  or a directory of your own; `--no-judge` skips the last pass.
- **A reference check that does not ask a model.** Whether an identifier
  resolves is decidable, so it is settled by searching the tree before any
  agent runs, and the answer goes into the shared prompt prefix once for the
  whole run.
- **Four report formats.** `md` and `html` for people, `sarif` for GitHub code
  scanning, `codequality` for the GitLab merge request widget. Reports land in
  `.roboviewer/runs/<timestamp>/`, with `latest` pointing at the most recent
  one.
- **A run a pipeline can read.** Exit `0` for nothing worth failing on, `1` for
  a confirmed finding at or above `--fail-on`, `2` when the tool could not run
  at all, and `3` when the review ran but a checklist item failed and its
  aspect went unreviewed. Only confirmed findings inside the changed lines can
  colour a job red, and `--fail-on` defaults to `never`: reporting is the job,
  failing the build is opt-in.
- **One config file.** `~/.config/roboviewer/config.toml`, or the file
  `--config` names — which replaces it rather than layering on top, so a
  pipeline and a laptop read the same settings from one place. `[provider]` is
  how to reach the gateway, `[reviewer]` and `[judge]` are what to ask of a
  model. `--show-config` prints what a run would use and where it came from,
  and `--check-provider` makes a handful of targeted requests and names what is
  wrong — the auth scheme, a `base_url` missing `/v1`, a gateway that cannot do
  tool calling — instead of leaving you to infer it from eight agents failing
  at once.
- **A key that stays out of the tree.** `ROBOVIEWER_API_KEY`, or a config file
  you keep outside the repository.
- **Findings in another language.** `--language ru` asks the model for its own
  text — titles, rationales, suggestions, the judge's summary — in a language
  other than English, without touching the prompts.
- **Rate limits handled before the refusal.** A review is a burst, and retrying
  into a full bucket does not help, so the run is held back before a request is
  sent. A 429 holds every agent back rather than only the one that was refused,
  and the ceilings most providers advertise on each response are picked up as
  they move.
- **A turn budget per agent.** An agent that runs out of turns says so, and its
  item is reported unreviewed rather than ticked off as clean.

Two things worth knowing before the first run:

- **A config key that nothing reads stops the run.** Unknown keys in
  `config.toml` are refused with `Config error:` naming the key, and exit code
  `2`. Silently ignoring them meant a mistyped setting left the run on defaults
  with no evidence anywhere that it had.
- **The endpoint has to do tool calling.** The agents drive the review through
  tools, so a completions-only gateway cannot run this at all.

Requires Python 3.11 or newer, and git.
