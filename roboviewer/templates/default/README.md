# Report templates

Jinja2. File names are `<document>.<format>.j2`; partials start with an
underscore. The second extension decides escaping: `.md.j2` is emitted as-is,
`.html.j2` escapes every value. A finding's title is model output quoted back,
so in HTML it must not become markup.

A custom set is pointed at by `templates_dir` in the config, or picked up from
`.roboviewer/templates/` inside the reviewed repository. Lookup is per file:
what you do not provide comes from the bundle, so an overridden `report.md.j2`
can still import the bundled `_finding.md.j2`. A document outside the tool works
the same way — `research/templates/` carries its own page and takes
`_layout.html.j2` and `_styles.css.j2` from here.

Which reports a run writes is `report_formats` in the config, or `--format
md,html`. A format is a module in `roboviewer/renders/`, one file per format —
but a module is only required when the render is not template-based (SARIF and
GitLab Code Quality are serialization, not documents). For a new templated
document, drop in `report.<name>.j2` and call `--format <name>`.

That name also sets the file extension, and with it the escaping. `--format
comment` writes `report.comment` unescaped; if the document is really HTML,
name the format `comment.html` — then the template is `report.comment.html.j2`,
the file is `report.comment.html`, and values are escaped.

A typo in a field name fails the render rather than emitting nothing: a report
with a silently missing section is worse than a failed run.

## What ships

| File | What it is |
|---|---|
| `report.md.j2` | The markdown report |
| `report.html.j2` | The same in HTML, extends `_layout.html.j2` |
| `_layout.html.j2` | Page skeleton: blocks `title`, `styles`, `content` |
| `_styles.css.j2` | Styles, inlined into `<style>` |
| `_finding.md.j2`, `_finding.html.j2` | The `finding(f)` macro — a single finding |

Markdown and HTML keep separate macros and do not try to share markup. They
share the data model, and that is enough.

HTML is built as one self-contained file: styles inline, no links, no scripts,
no external images. The report is opened by double click and attached to a
ticket, so it has nowhere to fetch from.

## What reaches a template

The model is assembled in `roboviewer/view.py`. It is a contract: fields get
added, renaming one breaks other people's templates.

| Name | What is inside |
|---|---|
| `meta` | `run_id`, `repo_root`, `branch`, `target`, `base_sha`, `head_sha`, `model`, `started_at`, `finished_at` |
| `stats` | `files_changed`, `added`, `removed`, `total_tokens`, `by_severity` — a list of `{severity, count}`, non-empty severities only, heaviest first |
| `cache` | `state` (`hit` / `zero` / `unknown`), `prompt_tokens`, `cached_tokens`, `hit_rate` |
| `judge_summary` | The judge's text, empty string if there was no judge |
| `findings` | Confirmed: `id`, `file`, `line`, `end_line`, `location`, `severity`, `category`, `confidence`, `title`, `rationale`, `suggestion`, `sources`, `verdict`, `verdict_reason` |
| `rejected` | The same for what the judge rejected |
| `items` | Checklist items: `id`, `title`, `status`, `findings_count`, `turns`, `total_tokens`, `duration_s`, `cache`, `error`, `summary` |
| `failed_items` | The subset of `items` with `status == "failed"` |
| `truncated_items` | The subset with `status == "truncated"` — the turn limit made them submit |
| `files` | `file`, `status`, `added`, `removed` |

`cache.state` tells three outcomes apart, not two. `unknown` is not a polite
zero: a gateway may serve the shared prefix from cache without counting it, and
then silence says nothing. `zero` is the only state that means caching really
did not work.

`verdict_reason` is filled only when the judge said something substantive: an
`unreviewed` verdict is not a judgement and must not be shown as one.

`status == "truncated"` is not a failure and not a pass. The agent did submit
findings, but on the last turn `tool_choice` left it no other move, so it
stopped because the turn limit said so. Rendering that as a tick turns "I ran
out of turns" into "I found nothing" — give it its own icon. An item's `summary`
is `None` when the agent submitted without a conclusion, which is the usual
shape of being cut off and worth saying out loud.

## What else is available

Dictionaries: `SEVERITY_LABEL`, `SEVERITY_ICON`, `STATUS_ICON` — keyed by the
matching field (`SEVERITY_ICON[f.severity]`).

Filters:

| Filter | Example |
|---|---|
| `thousands` | `167100` → `167 100` |
| `percent` | `0.67` → `67%` |
| `fixed` | `74.4` → `74`, `fixed(1)` → `74.4` |
| `size` | `6543` → `6.4 KB`, `812` → `812 B` |
| `blockquote` | multi-line text → every line prefixed with `> ` |
| `markdown` | markdown → HTML, HTML templates only |

`markdown` is needed because escaping alone is not enough in HTML: `rationale`
and `suggestion` are written in markdown, and without it backticks come out as
backticks. Raw HTML inside that text does not get through — only the markup the
parser produced itself is allowed.
