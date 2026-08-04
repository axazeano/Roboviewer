# Prompts

The files in this directory are the texts that go to the model verbatim.
Edit them freely; the placeholders in curly braces are substituted by the code.
Literal braces in the text must be doubled: `{{` and `}}`.

They are written in English, and so are the findings the model returns by
default — see [Output language](#output-language) to change that without
touching these files. This file (README.md) is not a prompt and is never shown
to the model.

| File | Who sees it | Placeholders |
|---|---|---|
| `item_system.md` | system prompt of the reviewer agent¹ | — |
| `item_user.md` | task for the reviewer agent | `{context}` `{item_title}` `{item_body}` |
| `judge_system.md` | system prompt of the judge² | — |
| `judge_user.md` | task for the judge² | `{context}` `{count}` `{findings}` |
| `judge_one_system.md` | system prompt of a per-finding judge³ | — |
| `judge_one_user.md` | task for a per-finding judge³ | `{context}` `{finding}` `{roster}` |
| `judge_final_system.md` | system prompt of the second-stage judge⁴ | — |
| `judge_final_user.md` | task for the second-stage judge⁴ | `{context}` `{count}` `{findings}` |

¹ A checklist set can replace the reviewer's system prompt with its own
`_system.md` in the set's directory — then `item_system.md` is not used for its
items.

² Used when `run.judge_mode = "batch"`, the default: one pass rules on the whole
list at once.

³ Used when `run.judge_mode = "per_finding"` or `"two_stage"`: one pass per
finding, each seeing a single claim. `{roster}` is a one-line-per-finding list of
everything else in the run, so a `duplicate` verdict is still possible; it is
assembled by the code and comes out empty when there is nothing else to list.

⁴ Used when `run.judge_mode = "two_stage"`, for the pass that follows those:
`{findings}` holds only the findings that survived verification, each carrying a
`Verified` line with what its own pass reported checking. This one rules on the
set — severity relative to the others, duplicates, the verdict on the MR — so it
is the text to edit when severities come out miscalibrated.

The `{context}` block — the MR header, the list of changed files, the files
themselves with markup, and the legend for it — is assembled by the code
(`prompts/__init__.py`) and is not edited here. It is assembly over diff fields
rather than wording, and the legend has to match the markup `gitdiff.py`
actually emits.

## Output language

`run.output_language` in the config, or `--language`, asks the model to write
its own prose — finding titles, rationales, suggestions, verdict reasons and the
summary — in a given language. It takes an ISO code or a name (`ru`, `Russian`,
`German`); anything the map in `prompts/__init__.py` does not recognise goes
into the prompt as written, so `Bahasa Indonesia` works too. Unset asks for
nothing and the model answers in the language of these files.

The directive is appended by the code, not spelled out in the files above: a
custom prompt set gets the option without having to carry a placeholder for it,
and a checklist set with its own `_system.md` keeps it too. It lands twice — in
the system prompt, and restated as the last line of the task, because a small
model drifts back to the language of the code it has been reading.

Report headings, tables and labels are not affected; those live in the report
templates and stay English.

## Overriding

You need only the files you change: each one is looked up in your directory
first, and the rest come from here. The directory is `.roboviewer/prompts/`
inside the reviewed repository (picked up on its own) or any other path set via
`run.prompts_dir` in the config.

`roboviewer --show-config` prints which file came from where.

A broken placeholder fails the run at startup, before the first request to the
model, and names the file — so a typo costs a second rather than eight failed
agents.
