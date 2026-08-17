# Tuning

Fitting the tool to a model: the texts the agents run on, how many agents run at
all, and what to read before turning a limit up.

## Prompts

The eight texts the agents actually run on are markdown files in
`roboviewer/prompts/default/`: a system prompt and a task for the reviewer, and
three such pairs for the judge — the batch pass, the per-finding verification
and the final calibration. Drop a changed copy of any of them into
`.roboviewer/prompts/` inside the repository being reviewed and it wins; the
rest keep coming from the bundled set, so a custom set carries only the files
it changes. `--show-config` prints which text came from where, and a typo in a
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
roboviewer develop                                 # default: 8 aspects, one agent each
roboviewer develop --checklist checklists/grouped  # 3 agents over related aspects
roboviewer develop --checklist checklists/single   # one agent for everything
```

Fewer agents means the context block is resent fewer times, which is most of the
token bill — at the cost of each agent holding more objectives at once. Smaller
models tend to lose the later aspects when asked to hold many. Compare the
`report.md` tables to see how the trade lands on your model.

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

## Why so little of this is a flag

Everything else here is a config setting rather than a flag, which is
deliberate: fitting the tool to a model is something you settle once and keep,
not something you retype per run, and `--help` is shorter for the people who
never do it. `judge_mode = "two_stage"` under `[run]` gives every finding its
own verification pass and then a judge over the survivors; a `[judge]` section
with a `model` of its own puts a stronger model on the verdicts. `--no-judge` stays on the command line, because skipping verification
is what you do while iterating on a prompt and want the raw output, and `-j`
stays because how many agents run at once is about the machine, not the model.
