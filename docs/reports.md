# Reports and output

What a run writes, where it writes it, which formats are available and how to
change what they look like.

`.roboviewer/runs/<timestamp>/` holds `report.md` for humans, plus
`findings.json` and per-item raw results for tuning prompts. `latest` symlinks to
the most recent run. Point `-o` somewhere outside the repository to keep it out
of `git status`.

`runs/` is the only thing under `.roboviewer/` that a run writes. `config.toml`,
`prompts/` and `templates/` are files you write, and a
[CI setup](ci.md) reads the first of them, so ignore the output and nothing
else:

```gitignore
.roboviewer/runs/
```

Ignoring `.roboviewer/` wholesale takes the committed config down with it, and
git says nothing when it does.

What a run *did* — the prompt each agent was given, what it opened, where its
turns went — is not written here and is not written by a review at all. It is a
question for tuning rather than for reading a report, and the instrument that
answers it lives beside the tool: [Watching a run](trace.md).

## Formats

`--format` picks what a run writes. One file per format in
`roboviewer/reports/renders/`:

| Format | File | For |
| --- | --- | --- |
| `md` | `report.md` | Reading in a terminal, pasting into a merge request |
| `html` | `report.html` | One self-contained file — opens by double click, attaches to a ticket |
| `sarif` | `report.sarif` | SARIF 2.1.0: GitHub Code Scanning, the VS Code viewer, SonarQube |
| `codequality` | `gl-code-quality-report.json` | The Code Quality widget in a GitLab merge request |

`md` and `html` are Jinja templates in `roboviewer/reports/templates/default/`; drop a
changed copy into `.roboviewer/templates/` to override one file by file, or add
a new `report.<name>.j2` and `--format <name>` picks it up without touching
Python. `sarif` and `codequality` are serialization rather than documents, so
they are plain Python and never go near a template.
