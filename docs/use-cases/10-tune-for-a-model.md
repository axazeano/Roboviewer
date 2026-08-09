# Tune the tool for a particular model

Someone points the tool at a model that is not the one it was developed
against, finds the reviews too shallow or too expensive, and starts turning
things.

## What serves it

| Surface | Part |
| --- | --- |
| `--model`, `provider.model` | which model |
| `--thinking on/off`, `provider.enable_thinking` | reasoning mode |
| `-j, --concurrency`, `run.concurrency` | items reviewed at once |
| `run.max_turns` | turn budget for one reviewer |
| `--no-judge`, `run.enable_judge` | skip verification entirely |
| `--judge-mode`, `run.judge_mode` | one pass over the list, or a pass per finding |
| `--judge-turns`, `run.judge_max_turns` | turn budget for one judging pass |
| `provider.judge_model`, `provider.judge_enable_thinking` | the judge may differ from the reviewer |
| `provider.temperature`, `provider.max_tokens` | sampling |
| `run.min_confidence` | drop findings below a confidence before the judge sees them |
| three bundled checklist sets | the same aspects distributed over 8, 3 or 1 agent |

## What it costs to learn

Six command-line flags and eleven settings, for one activity. This is the
largest single block of surface in the tool, and the least used: it exists
because tuning against a model needed every one of these to be movable in a
single run, and that is a developer's need rather than a user's.

**Three fields exist only to say "the judge may differ".** `judge_model`,
`judge_enable_thinking` and `judge_max_turns` each mirror a reviewer setting
and each resolve to it when unset. That is a pattern, not three decisions, and
it is invisible until you read `resolve_judge_model` and its two neighbours.

**`min_confidence` is a third filter that nobody set.** It drops findings below
a confidence before the judge runs, and defaults to `0.0`, so it does nothing.
It predates both the judge and the scope gate — which are the two mechanisms
that now decide what survives — and a number the model assigns to its own
output is the weakest of the three tests available.

**The three checklist sets are a measurement instrument.** They differ only in
how aspects are grouped between agents; the aspect texts are identical, so
running the same merge request through each compares structure rather than
wording. That is exactly what it was built for, and exactly what a person
installing the tool does not need on day one.

## Verdict: fold

Not drop. The knobs found real things — `--thinking off` is a threefold
speed-up on a reasoning model, and the measurement that `max_turns` 15 to 25
bought nothing while costing 67% more tokens only exists because the number was
movable. A general-purpose tool meeting an unknown model needs some of this.

But six flags on the front page for an activity with one practitioner is the
wrong proportion, and the three drafts filed say so:

- move the tuning knobs out of the command line and into the config, keeping
  only `--model`, so what remains on `--help` is what a first run needs;
- collapse the three judge-mirror fields into one statement that the judge
  follows the reviewer unless told otherwise;
- remove `min_confidence`, whose job the judge and the scope gate now do, and
  which has never been set to anything but its default.

The checklist sets stay as they are: they cost one line in the README and they
are the only way to answer "would fewer agents work better on my model", which
is the first question a new model raises.
