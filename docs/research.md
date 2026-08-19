# Watching a run

A report says what a review concluded. It says nothing about how it got there:
which prompt each agent was given, what it said between turns, which files it
opened and over what lines, what it searched for and whether the search found
anything. All of that lives in the process and dies with it, which is why a
prompt change that moves the findings cannot be explained from the artifacts.

The `research` package is the instrument for that question. It watches a run and
writes down everything the agents did, then renders one HTML page from it.

Like [the corpus builder](corpus.md), it is a step *beside* the tool: a separate
command, in a package that is not part of the wheel and is not on the review
path. Roboviewer itself keeps nothing and renders nothing — it reports what its
agents do through one neutral seam (`roboviewer/observe.py`), and nobody is
listening unless a command here started the run.

```bash
python -m research review develop            # a recorded run, then the page
python -m research review develop feature/x --no-judge
python -m research page .roboviewer/runs/latest
```

Run it from the repository root: the package ships with the source and is
deliberately not installed.

`review` takes the tool's own flags and passes them straight through, so there
is no second command line to keep in step; it exits with whatever the review
exited with. `page` renders a log that already exists — the run that was killed,
the run from last week, the run a colleague sent over.

Both write into the run's own directory, beside the report:

```
.roboviewer/runs/20260820-120000/
├── report.md
├── run.json
├── trace.jsonl      the log, written as the run happens
└── trace.html       the page rendered from it
```

## What the page shows

**The shape of the run first.** Agents, turns, reads, searches, tokens — then
the changed files, each saying how many agents opened it, or that none did. A
file nobody opened and nobody reported is not a file that came back clean, and
that is the difference the report cannot express. Files opened outside the
change are listed too: that is the agent going looking.

**Then one folded card per checklist item**, with its status, how many of its
turns it used, what it read and searched and what it cost. Opening it gives the
prompts it was handed, then the turns: what the model said, and under each reply
the calls it made — `read_file src/cart.py:1-200 → 200 lines · 6.4 KB`,
`grep Cart\( in *.py → 2 hits`, failures in red. At the bottom is the runner's
verdict: submitted, submitted because the turns ran out, or nothing submitted
and why, with the summary and what it handed back.

**The judge's passes are on the same page**, in their own section, in the same
shape — they are agents with a turn budget like any other.

Everything long starts collapsed. The page is for reading, not for archaeology.

## What the log is, and what it is not

`trace.jsonl` is one JSON object per line, written and flushed as the run
happens rather than assembled at the end: a run that dies mid-flight is exactly
the run worth reading, and its log is already on disk. The page renders from
whatever is there, and an agent that never reported an end is shown as still
running.

It records what each call **asked for** and **how much came back** — never what
came back. Tool output is repository content, and a log that kept it would be a
second copy of the repository that grows with what the agents read rather than
with what they did. A test holds that line: five calls returning 100 characters
and five returning 200 000 produce logs of the same size.

Prompts are stored once and referred to by hash. The judge asks one system
prompt of every finding it verifies, and thirty copies of it would be thirty
copies for nothing; on the page the second agent to be handed a prompt says
whose section carries it.

## What reaches the page's template

`research/templates/trace.html.j2` is a Jinja template like the tool's own, and
it extends the tool's `_layout.html.j2` — one skeleton, not two that drift. The
model it renders is assembled in `research/view.py` from the log alone.

| Name | What is inside |
|---|---|
| `meta` | `run_id`, `branch`, `target`, `base_sha`, `head_sha`, `model`, `started_at` |
| `stats` | `agents`, `turns`, `calls`, `reads`, `searches`, `total_tokens`, `files_changed`, `files_opened`, `unfinished` |
| `files` | Changed files: `file`, `status`, `added`, `removed`, `readers` — how many agents opened it, zero included |
| `elsewhere` | Paths opened during the run that the merge request did not change |
| `items` | One agent per checklist item, in checklist order |
| `judge` | The judging passes, in the order they started |

An agent carries `title`, `status`, `error`, `summary`, `system`, `prompt`,
`system_chars`, `prompt_chars`, `turn_count`, `max_turns`, `calls`, `reads`,
`searches`, `total_tokens`, `duration_s`, `opened`, `findings`, `verdicts`, and
`turns` — each turn with `n`, `text`, `preview`, `tokens` and its `calls`. A call
is `tool`, `subject` (the arguments on one line), `chars`, `lines`, `hits`,
`error` and `seconds`.

`system` and `prompt` are empty when another agent was given the same text;
`system_same_as` and `prompt_same_as` then name whose section carries it.

## Writing another instrument

`roboviewer/observe.py` is the whole seam: `RunObserver` is told when a run
opens (with the run and the directory its artifacts go in), hands out one
`AgentObserver` per agent, and is told when the run closes. An agent observer
hears the prompts it was given, every reply with its usage, every tool call with
its whole answer, and the outcome.

What to keep out of any of that is the observer's decision. `research` measures
tool output and drops it; something counting cache hits per turn, or comparing
two runs of one configuration, would keep something else. The tool has no
opinion, which is the point: nothing here can slow a review down or leave a file
somebody did not ask for.
