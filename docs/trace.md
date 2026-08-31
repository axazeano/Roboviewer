# Watching a run

A report says what a review found. This says what it did on the way: which
prompt each agent got, what it said between turns, which files it opened and
what it searched for.

Not part of the wheel — run it from the
repository root.

## Commands

```bash
python -m measure.trace review --into develop        # run a review, keep a log, render the page
python -m measure.trace page .roboviewer/runs/latest  # render a log that already exists
```

`review` takes the tool's own flags and passes them through, and exits with
whatever the review exited with:

```bash
python -m measure.trace review --from feature/login --into develop  # someone else's branch
python -m measure.trace review --into develop --no-judge           # while iterating on a prompt
python -m measure.trace review --into develop --only correctness   # one checklist item
python -m measure.trace review --into develop --repo ~/projects/app  # a repository elsewhere
```

Both write into the run's own directory, beside the report:

```
.roboviewer/runs/20260820-120000/
├── report.md
├── run.json
├── trace.jsonl      the log, written as the run happens
└── trace.html       the page rendered from it
```

A run that was killed still has its log, so `page` works on it too — the agents
that never finished are shown as still running.

## The page

Open `trace.html` by double click. Top to bottom:

- **the shape of the run** — agents, turns, reads, searches, tokens;
- **changed files**, each saying how many agents opened it, or that none did,
  plus what was opened outside the change;
- **one folded card per checklist item** — status, turns used, reads, searches,
  tokens, seconds. Open it for the prompts it was given, then the turns: what
  the model thought (`THINKING`), what it said, and the calls it made —
  `read_file src/cart.py:1-200 → 200 lines · 6.4 KB`, `grep Cart\( in *.py → 2 hits`.
  At the bottom, what it handed back;
- **the judge's passes**, in the same shape.

## Reading the log directly

`trace.jsonl` is one JSON object per line. Anything the page shows can be asked
of it, and a few things the page does not.

Which files did the run actually open, and how often:

```bash
jq -r 'select(.t=="call" and .tool=="read_file" and .error==false) | .args.path' trace.jsonl | sort | uniq -c | sort -rn
```

```
   2 src/cart.py
   1 src/api.py
```

Where the budget went, per agent:

```bash
jq -r 'select(.t=="outcome") | "\(.a)  \(.status)  \(.turns) turns  \(.usage.prompt_tokens + .usage.completion_tokens) tokens"' trace.jsonl
```

```
a1  ok  3 turns  52290 tokens
a2  truncated  15 turns  142100 tokens
a3  failed  1 turns  9010 tokens
```

Which agent is which:

```bash
jq -r 'select(.t=="agent") | "\(.a)  \(.kind)  \(.title)"' trace.jsonl
```

```
a1  item  Correctness and logic errors
a2  item  Error handling
a4  judge  judge F001
```

Searches that came back with nothing — a prompt that sends agents hunting for
what is not there:

```bash
jq -r 'select(.t=="call" and .tool=="grep" and .hits==0) | .args.pattern' trace.jsonl | sort | uniq -c | sort -rn
```

Calls that failed:

```bash
jq -r 'select(.t=="call" and .error) | "\(.tool) \(.args.path // .args.pattern)"' trace.jsonl
```

How the prompt grew turn by turn for one agent:

```bash
jq -r 'select(.t=="turn" and .a=="a1") | "turn \(.n): \(.usage.prompt_tokens) prompt tokens"' trace.jsonl
```

## Record types

One `t` per line.

| `t` | Fields | One per |
| --- | --- | --- |
| `run` | `run_id`, `branch`, `target`, `base_sha`, `head_sha`, `model`, `started_at`, `files`, `items` | run |
| `blob` | `h`, `text` — a prompt, stored once and referred to by hash | distinct prompt |
| `agent` | `a`, `kind` (`item`/`judge`), `title`, `item_id`, `system`, `prompt` (blob hashes), `max_turns` | agent |
| `turn` | `a`, `n`, `text`, `thinking`, `usage` | model reply |
| `call` | `a`, `n`, `tool`, `args`, `chars`, `lines`, `hits`, `error`, `seconds` | tool call |
| `outcome` | `a`, `status`, `turns`, `duration_s`, `usage`, `error`, `summary`, `findings`, `verdicts` | agent |

A call records what was **asked for** and **how much came back** — never what
came back. Tool output is repository content, and a log that kept it would grow
with what the agents read rather than with what they did.

## Changing the page

`measure/trace/templates/trace.html.j2` is an ordinary Jinja template; it extends the
tool's `_layout.html.j2`, so the styles and filters are the same ones the report
uses. The model it renders comes from `measure/trace/view.py`:

| Name | What is inside |
| --- | --- |
| `meta` | `run_id`, `branch`, `target`, `base_sha`, `head_sha`, `model`, `started_at` |
| `stats` | `agents`, `turns`, `calls`, `reads`, `searches`, `total_tokens`, `files_changed`, `files_opened`, `unfinished` |
| `files` | Changed files: `file`, `status`, `added`, `removed`, `readers` |
| `elsewhere` | Paths opened during the run that the merge request did not change |
| `items` | One agent per checklist item, in checklist order |
| `judge` | The judging passes, in the order they started |

An agent carries `title`, `status`, `error`, `summary`, `system`, `prompt`,
`system_chars`, `prompt_chars`, `turn_count`, `max_turns`, `calls`, `reads`,
`searches`, `total_tokens`, `duration_s`, `opened`, `findings`, `verdicts`, and
`turns` — each turn with `n`, `text`, `thinking`, `preview`, `tokens`, `ended`
and its `calls`. A call is `tool`, `subject`, `chars`, `lines`, `hits`, `error`,
`seconds`.

`system` and `prompt` are empty when another agent was handed the same text;
`system_same_as` and `prompt_same_as` then name whose section carries it.

## Watching a run yourself

The tool itself keeps nothing. It reports through `roboviewer/observer.py`, and
`measure.trace` is one listener; here is another, counting how often each file is
opened:

```python
from collections import Counter
from roboviewer.cli import main

class Opens:
    def __init__(self): self.files = Counter()
    def opened(self, run, directory): pass
    def closed(self): print(self.files.most_common())
    def agent(self, kind, title, item_id=""): return self

    def started(self, *, system, prompt, max_turns): pass
    def replied(self, turn, text, usage, thinking=""): pass
    def called(self, turn, tool, args, output, seconds):
        if tool == "read_file":
            self.files[args.get("path")] += 1
    def finished(self, **kwargs): pass

main(["develop"], observer=Opens())
```

`called` is handed the tool's whole answer; what to keep out of it is the
observer's decision. `measure.trace` measures it and drops it.
