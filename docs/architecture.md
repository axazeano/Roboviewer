# Architecture

The map of the code: what each package is for, what may depend on what, and
where to start reading. Written for the person who opens a file and wants to
know where they are without reading three others.

## The shape of a run

```
roboviewer <target> [source]
  │
  ├─ cli        parse the flags, load the config, gather the change,
  │             build the plan, run it, write the reports, decide the exit code
  ├─ repo       read git: which branches, which files and lines changed, the
  │             files in full with markup, what the diff references that
  │             resolves to nothing → ChangeSet
  ├─ review     one agent per checklist item over the ChangeSet, merge what
  │             they reported, keep what is about the change, let the judge
  │             rule → ReviewRun
  │    └─ provider   each agent is a tool-calling loop against an
  │                  OpenAI-compatible gateway, paced against its limits
  └─ reports    ReviewRun → view → report.md / report.html / SARIF / Code Quality
```

Two things cut across all of it: `models.py`, the vocabulary every package
speaks (a finding, a verdict, a run, token usage), and `observer.py`, the one
protocol a run reports through — the console prints from it, `measure.trace`
records from it, and the pipeline knows neither.

## The packages

| Package | What is in it | Start reading at |
| --- | --- | --- |
| `roboviewer/config/` | The sections of the two config files (`settings`), how they are found and read (`loading`), where the overridable file sets come from (`overrides`) | `settings.py` |
| `roboviewer/repo/` | Everything that touches git. One wrapper (`git`), the diff (`diff`), files rendered with markup (`annotate`), the reference pre-pass (`references`), the agent's read-only tools (`tools`), and `ChangeSet` put together from them (`changeset`) | `changeset.py` |
| `roboviewer/provider/` | The gateway. The runner contract (`runner`), the OpenAI tool-calling loop (`openai_agent`), what the config turns into on a request (`request`), token usage out of an answer (`usage`), pacing (`ratelimit`), the diagnostic probe (`probe`) | `runner.py` |
| `roboviewer/review/` | The review. The order of the stages (`pipeline`), the checklist (`checklist`), what the agents hand back (`submissions`), the merge (`merge`), the scope gate (`scope`), the judge (`judge`) | `pipeline.py` |
| `roboviewer/review/prompts/` | Everything the model reads: the eight texts in `default/`, the context block (`context`), findings as a judge sees them (`findings`), the language directive (`language`), the tool descriptions (`tool_schemas`), what the runner says between turns (`turns`), and the loader and builders (`assembly`) | `default/README.md` |
| `roboviewer/reports/` | The view a report is rendered from (`view`), one module per format (`renders/`), the Jinja templates (`templates/`), and writing the run to disk (`save`) | `view.py` |
| `roboviewer/cli/` | The command: the flow (`main`), the flags (`arguments`), everything printed (`console`), the exit codes (`exit_codes`), the CI detection (`ci_env`), `--check-provider` (`check_provider`) | `main.py` |
| `roboviewer/benchmark/` | The `benchmark` command, beside the review rather than on its path: the index (`items`), the references (`references`), where everything lives (`store`), one entry into a clone (`fetch`, `clone`), what GitHub knows (`github`, `candidates/`), the tool over every entry (`run`), the command (`cli`) | `__init__.py` |
| `roboviewer/checklists/` | The bundled checklist sets — data, read by `review.checklist` and named on the command line (`--checklist checklists/grouped`), which is why they stay at the package root | `default/` |
| `benchmarks/` | The benchmark's data, at the repository root: `items.toml` the index of merge requests, `references/<id>.toml` what a good review of one finds — both committed; `repos/`, `comments/` and `runs/` are the cache the command fills | `items.toml` |
| `measure/` | The instrument beside the tool, outside the wheel: `trace/` watches a run and renders what its agents did | `__init__.py` |

## The dependency rule

Lower may not import higher. Within `roboviewer/`:

```
benchmark
cli
reports
review            (review/prompts inside it)
provider   repo
config     observer
models
```

- `models` imports nothing of ours. `observer` imports `models`.
- `config` imports nothing of ours but `models`-free helpers; it knows no SDK
  — what the config turns into on a request lives in `provider.request`.
- `provider` and `repo` do not know each other. The loop in `provider` runs
  the tools in `repo.tools` because the review hands it their names; the
  texts it says between turns arrive on the request as `TurnNotes`.
- `review` is the only package that imports both `provider` and `repo`, and
  the only one that knows what a prompt is.
- `reports` reads `models` and nothing of the review's internals: `ReviewRun`
  is the whole contract.
- `cli` imports everything and is imported by nothing but `__main__`,
  `benchmark.cli` and `measure.trace.cli`.
- `benchmark` sits beside `cli`, not under it: it runs the tool through
  `cli.main`, reads `models` and `observer`, and nothing on the review path
  imports it. It is the only package that talks to a forge.
- `measure` imports `roboviewer` through public surfaces only (`models`,
  `observer`, `config`, `cli.main`, `reports.renders`). Nothing in
  `roboviewer` imports `measure`.

A test holds the layout to one more rule: in every module, public definitions
come before private ones (`tests/test_layout.py`). A reader meets what a
module offers first and how it does it after.

## Where the seams are

- **`ChangeSet`** (`repo.changeset`) is what the review reads about the code:
  `comparison` (what against what), `files` and `lines` (what changed),
  `attachments` (what the agent is shown), `references` (what resolves to
  nothing). Anything that needs git goes through `repo`; nothing above it
  shells out.
- **`Runner`** (`provider.runner`) is what the review asks of a provider: run
  one `AgentRequest`, return one `AgentOutcome`. Tests drive the pipeline with
  a scripted runner and never touch the network.
- **`RunObserver` / `AgentObserver`** (`observer`) is how a run is watched.
  `Observer` is the no-op base; `Broadcast` fans out to several.
- **`ReviewRun`** (`models`) is what a finished review is. The reports render
  it, `save` writes it, the gate reads it.
- **`ReviewView`** (`reports.view`) is the contract with user templates — see
  `reports/templates/default/README.md` — and renaming a field there breaks
  other people's reports.

## What is deliberately not here

- No forge client on the review path: `roboviewer` reads two git branches and
  never talks to GitHub or GitLab. The only forge code is `roboviewer.benchmark`,
  which reviews nothing itself — it fetches what the tool is measured on and
  runs the tool over it.
- No state between runs; every run starts from the diff.
- No prompt text outside `review/prompts/`: the tool descriptions, the
  annotation legend, the turn budget notes and the context block are all
  there, next to the eight markdown texts.

## Conventions

- Module docstrings say what the module is for and why it is shaped the way
  it is; function docstrings say what a caller gets. Comments are short and
  in English.
- Public before private within a module (enforced by a test).
- Behaviour is pinned by the suite: golden reports, prompt texts, exit codes,
  config keys, report file names, the trace log format. A change to the layout
  is not a change to any of those.
- `ruff check .`, `mypy` and `pytest -q` are what CI runs; all three stay
  green on every commit.
