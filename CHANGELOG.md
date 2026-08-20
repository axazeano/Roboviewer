# Changelog

What changed between releases, newest first.

A release is a `Release/<version>` tag on `main`. The tag runs the suite,
refuses to publish when its number and the version in `pyproject.toml`
disagree, and then puts the wheel on [PyPI][pypi] and the image on
[Docker Hub][docker]. Versions follow [semantic versioning][semver].

[pypi]: https://pypi.org/project/roboviewer/
[docker]: https://hub.docker.com/r/axazeano/roboviewer
[semver]: https://semver.org

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
