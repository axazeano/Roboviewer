# Use cases

What someone sets out to do with this tool, one document each, with a verdict
on what it costs them.

Most of the surface here arrived while tuning a model against reference merge
requests. Every knob earned its place there by making a measurement possible,
which is not the bar a released tool holds. This directory is the record of
asking each one to justify itself again.

## The list, frozen 2026-08-08

| | Use case | Verdict |
| --- | --- | --- |
| 1 | [Set it up against a gateway](01-set-up.md) | keep |
| 2 | [Find out why the gateway does not answer](02-diagnose-the-gateway.md) | keep |
| 3 | [Review my branch before opening the merge request](03-review-my-branch.md) | keep |
| 4 | [Review a branch I have not checked out](04-review-elsewhere.md) | keep |
| 5 | [See what would be reviewed without paying for it](05-look-before-paying.md) | keep |
| 6 | [Run inside a pipeline and fail the build](06-run-in-ci.md) | keep |
| 7 | [Read the result](07-read-the-result.md) | keep |
| 8 | [Get findings in my language](08-another-language.md) | keep |
| 9 | [Fit the review to my codebase](09-fit-my-codebase.md) | fold |
| 10 | [Tune the tool for a particular model](10-tune-for-a-model.md) | fold |

The list is frozen: anything found after this date becomes a new document and a
new task, not another line here. A list that grows while it is being worked
never closes.

## How each one is judged

Two questions, and neither is "is this flag used".

**What does it cost to learn?** Counted in concepts a person meets before they
can do the thing they came for — not in flags. Four flags that are one idea
cost less than two flags that are two ideas.

**Can the behaviour be read in one place?** A setting whose effect is assembled
from several sources, or that changes what happens by existing rather than by
being named, costs more than its line count suggests.

A verdict is one of three:

- **keep** — the use case is worth offering and its surface is proportionate.
- **fold** — worth offering, but the surface is larger than the idea. Something
  merges, defaults, or moves out of the command line.
- **drop** — not worth offering at this bar.

Removing nothing would have been an acceptable outcome. Leaving something
unexamined would not.

## What this examination did not find

No dead configuration. Every `ProviderConfig` and `RunConfig` field has a
reader, checked by searching for callers rather than by reading intent. Two
looked unread to a plain search for the attribute — `judge_model` and
`extra_body` — and both are reached through a resolver method
(`resolve_judge_model`, `request_body`). `auth_value` has no caller outside
`config.py`, where `request_headers` uses it, which is what a private helper
looks like.

So nothing here is an argument about dead code. Everything below is an argument
about how much a person has to hold in their head.

## What happens to a verdict

Nothing, until it is approved. A conclusion that something should go is filed
as a draft in the backlog and waits; examining and deleting are separate jobs,
and an examination that arrives with the deletions already made leaves no point
at which the reasoning can be disagreed with.

Seven are waiting, none of them started:

| From | Draft |
| --- | --- |
| 5 | say what a config loses when it replaces `exclude_globs` |
| 9 | fold the five context-budget numbers into something reasonable about |
| 9 | let the scope margin carry its own off state |
| 9 | require the prompt and template directories to be named, not discovered |
| 10 | move the tuning knobs off the command line |
| 10 | collapse the three judge-mirror settings into one rule |
| 10 | remove `min_confidence` |

Two of the ten cases produced six of the seven. That is the shape of the
result: the surface is not spread evenly and thinly, it is concentrated in the
two things a first-time user does not do.
