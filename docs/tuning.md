# Tuning

Fitting the tool to a model: the texts the agents run on, how many agents run at
all, and what to read before turning a limit up.

## Prompts

The eight texts the agents actually run on are markdown files in
`roboviewer/review/prompts/default/`: a system prompt and a task for the reviewer, and
three such pairs for the judge — the batch pass, the per-finding verification
and the final calibration. Drop a changed copy of any of them into
`.roboviewer/prompts/` inside the repository being reviewed and it wins; the
rest keep coming from the bundled set, so a custom set carries only the files
it changes. `roboviewer show-config` prints which text came from where, and a typo in a
placeholder fails the run before the first request instead of eight agents
deep.

Prompts and templates are not configuration, even though `.roboviewer/` holds
both. These two directories **are** picked up from the repository under review
just by existing, and they merge with the bundled set file by file. The config
file does neither: it is read only where you point it. Same directory, opposite
rules.

## How many agents

Three checklist sets ship with the tool, differing only in how the aspects are
distributed between agents — the aspect texts themselves are identical, so
running the same MR through each compares structure rather than wording:

```bash
roboviewer review --into develop                   # default: 8 aspects, one agent each
roboviewer review --checklist checklists/grouped   # 3 agents over related aspects
roboviewer review --checklist checklists/single    # one agent for everything
```

Fewer agents means the context block is resent fewer times, which is most of the
token bill — at the cost of each agent holding more objectives at once. Smaller
models tend to lose the later aspects when asked to hold many. Compare the
`report.md` tables to see how the trade lands on your model.

## Reading what the agents actually did

When a prompt change moves the findings, the report will not say why. Run the
review through the `measure.trace` package instead and it keeps an account of itself:

```bash
python -m measure.trace review develop
```

`trace.html` lands beside the report: per checklist item, the prompt the agent
was given, then turn by turn what it said and which files it opened, searched or
listed, and the verdict it came back with. That is where the reason usually is —
an agent that greps instead of opening the file it was handed, one that spends
six of its turns in one place, one that starts concluding before it has read
anything.

It also answers the question the report cannot: which changed files were opened
at all. A file nobody opened and nobody reported is not a file that came back
clean. See [Watching a run](trace.md).

## Speed

A slow run is rarely slow for the reason it looks like. Resending the same
context block to eight agents is the visible cost and usually not the real one:
providers serve a repeated prefix from cache. The time goes into the model
thinking, token by token, on every turn. `enable_thinking = false` under
`[reviewer]` runs a reasoning model with that switched off — a large speedup,
and a large risk to depth, so it belongs on a merge request whose problems are
already known.

## The turn limit

Watch the status column for ⚠️. On its last turn an agent is forced to submit
whatever it has, so an aspect that ran out of `reviewer.max_turns` hands back a
thin result that reads exactly like a clean pass. The report calls those out under
*Cut off by the turn limit*, together with whatever conclusion each one reached.

Read that conclusion before raising `reviewer.max_turns`. An agent is told its
budget and asked to land before it runs out, but if it still gets cut off while its summary
already reads as finished, it was not short of turns — it never stopped, and a
bigger budget buys nothing. Measured on a 64-file MR: 15 → 25 turns left the
same seven of eight agents cut off and cost 67% more tokens.

## A finding with no line

Every finding is asked to name one line in the new version of its file, and the
`submit_findings` schema lists `line` as required. The two have to say the same
thing: where a prompt and a schema disagree the schema wins, because it is what
function calling enforces, and a model that fills optional fields last leaves
an optional line out — on one model, every finding of three benchmark runs came
back without one. With the field required the same model named a real line on
every finding; see [Findings with a line](measurements.md#findings-with-a-line).

A run still does not refuse such a finding. `0`, an empty string and anything
that is not a number arrive as no line at all, and the finding is kept: a claim
about a file is worth reading even when it is badly anchored. What it cannot do
is pass for a located one. Its location reads `path/to/file (no line)`
everywhere the run prints one — the report, the console, the judge's prompt,
the comment body — and the rest of the run treats it as being about the file
as a whole: the scope gate lets it through when the MR touched that file, SARIF
attaches it to the file without a region, GitLab Code Quality puts it on line 1
because the format has no other way, and `roboviewer comment` writes it into
the body of the review instead of onto a line. The benchmark summary counts the
findings with a line per review, so a model that stops naming them shows up in
a column rather than in a report nobody reads closely.

Many findings without a line in one run are worth a look in the trace: either
the agents are racing the turn limit and submitting thin, or they never read
the numbered column they were handed.

## Why so little of this is a flag

Everything else here is a config setting rather than a flag, which is
deliberate: fitting the tool to a model is something you settle once and keep,
not something you retype per run, and `--help` is shorter for the people who
never do it. `judge_mode = "two_stage"` under `[run]` gives every finding its
own verification pass and then a judge over the survivors; a `[judge]` section
with a `model` of its own puts a stronger model on the verdicts. `--no-judge` stays on the command line, because skipping verification
is what you do while iterating on a prompt and want the raw output, and `-j`
stays because how many agents run at once is about the machine, not the model.
