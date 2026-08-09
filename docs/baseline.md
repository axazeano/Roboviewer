# Baseline before the simplification work

**Historical snapshot.** Everything from "Totals" down was measured on commit
`e105f0d` and is left as it was written; it is not kept current. The section
directly below is the second measurement the snapshot existed to make possible.

Measured with the tool configuration in `pyproject.toml`. Nothing here is a
target in itself — the point is that a later measurement can say whether a
change made the code smaller or only moved it.

```bash
pip install -e ".[dev]"
ruff check .                     # style, bugs, cyclomatic complexity
mypy                             # types; files and plugins come from pyproject
complexipy roboviewer            # cognitive complexity, which ruff has no rule for
pytest -q
```

The snapshot recorded that the thresholds were configured but enforced nowhere:
no CI, no pre-commit hook, so a failing count was a finding to read rather than
a build to break. Half of that is now false — TASK-2 put ruff, mypy and pytest
in front of every push and pull request, and TASK-3 brought the counts to zero.
There is still no pre-commit hook, and `complexipy` is still not in the
pipeline: nothing enforces cognitive complexity.

## The second measurement

After TASK-3, on the same commands:

| Measurement | `e105f0d` | now |
| --- | --- | --- |
| ruff findings | 116 (18 auto-fixable) | 0 |
| mypy errors | 7 in 3 files, 24 files checked | 0, 30 files checked |
| cognitive complexity, package total | 718 over 193 functions | 680 over 257 functions |
| functions over the default limit of 15 | 8 | 2 |
| package size | 4804 lines over 20 files | 5597 lines over 30 files |
| tests | 176 passed | 247 passed |

Two functions are still over the cognitive limit, and only one of them is from
the original eight: `checklist.py::load_checklist`, unchanged at 19. The other
seven are under the limit now, the three worst included — `change_map` at 30,
`run` at 27, `_unresolved_symbols` at 26. The second one over is
`resolve.py::_rule_matches` at 18, which did not exist at the baseline; it came
out of `_resource_misses`, which was 24 then and is under the limit now. So the
count went from eight to two, but one of the two is newly introduced rather than
left over.

This answers the question the baseline was taken to answer, and the answer is
not the flattering one. The package grew: 800 more lines across ten more files.
What fell is complexity per function — 3.7 down to 2.6 — because the growth went
into more, smaller functions rather than into the existing ones. So the code did
not get smaller. It got bigger and less tangled, which is a different claim and
the only one these numbers support.

Two counts moved because the bar moved rather than the code, and it is recorded
in `pyproject.toml`: `max-args` is 6 rather than pylint's default 5, so PLR0913
now counts a six-argument function as fine. Nothing else was silenced — no new
`noqa` comments, and no per-file ignores beyond the ones for `tests/*` that were
already there.

## Totals

| Measurement | Value |
| --- | --- |
| ruff findings | 116 (18 auto-fixable) |
| mypy errors | 7 in 3 files, 24 files checked |
| cognitive complexity, package total | 718 over 193 functions |
| functions over the default limit of 15 | 8 |
| package size | 4804 lines over 20 files |
| tests | 176 passed |

## ruff

| Rule | Count | What it is |
| --- | --- | --- |
| E501 | 65 | lines over 100 characters |
| C901 | 8 | cyclomatic complexity over 8 |
| PLR0913 | 8 | more than 5 arguments |
| UP037 | 7 | quoted annotations no longer needed |
| ARG001/ARG002 | 5 | unused arguments |
| I001 | 3 | import order |
| UP017 | 3 | `datetime.timezone.utc` → `datetime.UTC` |
| PLR0911 | 2 | more than 6 return statements |
| RUF005, RUF015, C416, UP035, UP042 | 2 each | small idiom fixes |
| PLR0912, PLR0915, RUF022, SIM300, SIM905 | 1 each | |

Cyclomatic complexity and shape, in full:

| Place | Finding |
| --- | --- |
| `cli.py:350` `main` | complexity 17, 13 returns, 16 branches, 51 statements |
| `cli.py:154` `_apply_overrides` | complexity 11 |
| `gitdiff.py:179` `change_map` | complexity 12 |
| `openai_agent.py:82` `run` | complexity 11 |
| `pipeline.py:63` `merge_findings` | complexity 10 |
| `openai_agent.py:200` `_complete` | complexity 9, 6 arguments |
| `pipeline.py:357` `_verify_each` | complexity 9 |
| `resolve.py:212` `_unresolved_symbols` | complexity 9 |
| `tools.py:344` `dispatch` | 8 returns, 6 arguments |
| `gitdiff.py:370` `collect` | 9 arguments |
| `gitdiff.py:282` `build_annotated` | 7 arguments |
| `gitdiff.py:114`, `tools.py:78`, `pipeline.py:163`, `cli.py:320` | 6 arguments each |

## Cognitive complexity

Per file, worst first:

| File | Total | Lines |
| --- | --- | --- |
| pipeline.py | 107 | 596 |
| resolve.py | 95 | 342 |
| gitdiff.py | 82 | 430 |
| diagnose.py | 77 | 338 |
| cli.py | 74 | 457 |
| runners/openai_agent.py | 70 | 343 |
| tools.py | 46 | 384 |
| prompts/\_\_init\_\_.py | 34 | 389 |
| config.py | 32 | 275 |
| checklist.py | 31 | 106 |

The eight functions over the default limit of 15:

| Function | Score |
| --- | --- |
| `gitdiff.py::change_map` | 30 |
| `openai_agent.py::OpenAIAgentRunner.run` | 27 |
| `resolve.py::_unresolved_symbols` | 26 |
| `pipeline.py::merge_findings` | 24 |
| `resolve.py::_resource_misses` | 24 |
| `cli.py::main` | 23 |
| `diagnose.py::_report_tool_modes` | 20 |
| `checklist.py::load_checklist` | 19 |

## mypy

Seven errors, and each one names something real rather than a missing
annotation:

- `models.py:91` — the `# type: ignore[arg-type]` on `int(v)` covers the wrong
  error code, and the ignore itself is unused.
- `renders/__init__.py:69` — `_CustomTemplate` is a frozen dataclass, so its
  attributes are read-only and it does not satisfy the `Render` protocol, whose
  members are settable.
- `pipeline.py:262` — `keep` and `drop` have no annotation.
- `pipeline.py:493` — `verified.reason` is read on a value that can be `None`.
- `pipeline.py:563` — a `str` indexes a `Counter` keyed by a `Literal`.

## What these numbers do not see

Both complexity metrics are per function. The cost that shows up when a feature
is spread across modules — one option adding a branch to `_apply_overrides`, a
field to `RunConfig`, a line to `_print_config` and a section to two templates —
lands entirely on the function that grew, and the modules it is coupled to score
nothing for it.
