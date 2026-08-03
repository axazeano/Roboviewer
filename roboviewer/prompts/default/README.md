# Prompts

The four files in this directory are the texts that go to the model verbatim.
Edit them freely; the placeholders in curly braces are substituted by the code.
Literal braces in the text must be doubled: `{{` and `}}`.

They are written in Russian, and so are the findings the model returns. This
file (README.md) is not a prompt and is never shown to the model.

| File | Who sees it | Placeholders |
|---|---|---|
| `item_system.md` | system prompt of the reviewer agent¹ | — |
| `item_user.md` | task for the reviewer agent | `{context}` `{item_title}` `{item_body}` |
| `judge_system.md` | system prompt of the judge | — |
| `judge_user.md` | task for the judge | `{context}` `{count}` `{findings}` |

¹ A checklist set can replace the reviewer's system prompt with its own
`_system.md` in the set's directory — then `item_system.md` is not used for its
items.

The `{context}` block — the MR header, the list of changed files, the files
themselves with markup, and the legend for it — is assembled by the code
(`prompts/__init__.py`) and is not edited here. It is assembly over diff fields
rather than wording, and the legend has to match the markup `gitdiff.py`
actually emits.

## Overriding

You need only the files you change: each one is looked up in your directory
first, and the rest come from here. The directory is `.roboviewer/prompts/`
inside the reviewed repository (picked up on its own) or any other path set via
`run.prompts_dir` in the config.

`roboviewer --show-config` prints which file came from where.

A broken placeholder fails the run at startup, before the first request to the
model, and names the file — so a typo costs a second rather than eight failed
agents.
