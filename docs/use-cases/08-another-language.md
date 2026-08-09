# Get findings in my language

The prompts are English, so findings come back in English. A team that works in
another language wants the titles, rationales and the judge's summary in it.

## What serves it

| Surface | Part |
| --- | --- |
| `--language LANG`, `run.output_language` | the language the model writes in |

## What it costs to learn

One flag, taking an ISO code or a name, with anything unrecognised passed
through as written so an unlisted language still works.

The design decision behind it is worth stating because it is what keeps the
cost at one flag: the directive is appended by the code at the end of the
system prompt and as the last line of the task, rather than being translated
into the prompts. So it survives a custom prompt set and a checklist that
brings its own `_system.md`, and a person who has forked the prompts does not
lose the option or have to carry a placeholder for it.

What it does not cover is stated in the README: report headings, table columns
and severity labels come from the templates and stay English. That is a real
seam, and it is honest about it.

## Verdict: keep

One flag, one config field, no interaction with anything else. The cheapest
case in the set.
