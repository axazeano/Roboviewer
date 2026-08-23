# Measurements

What the reviewer actually found, on one merge request, measured against a
hand-established truth. Numbers here are a starting line, not a claim: they are
one repository, one branch, and they are meant to move from release to release.

Read [the caveats](#how-to-read-these-numbers) before quoting any of it.

## The merge request everything was run against

| | |
| --- | --- |
| Merge request | [nextcloud/ios#4091](https://github.com/nextcloud/ios/pull/4091) — Albums on mobile app customisation |
| Branch → target | `nextmcloud:nmc/albums_customisation` → `nextcloud:master` |
| Merge base / HEAD | `3fe3a8d1432c` / `826f10c7ac32` |
| Size | 35 files and +4227/−94 on GitHub; 32 files and +3816/−87 after `exclude_globs` drops storyboards, strings, asset catalogues and the pbxproj |
| Reference | 32 defects in [`references/ios-4091.toml`](../benchmarks/references/ios-4091.toml), plus 3 claims proven false — the benchmark entry `ios-4091` |

Counting findings and counting defects are not the same thing, and the gap is
large enough to change conclusions. The nemotron configuration ships 48
findings, of which 36 are one "no tests" entry per file: one thought, thirty-six
rows. Every chart below is drawn per distinct defect for that reason.

Of the 31 known defects, 13 are of a class a compiler finds — unresolved
symbols, a file outside the build manifest, outlets with no storyboard. They are
in the truth set because they are real, not because a reviewer is the right tool
for them.

## Runs

Every series is five runs of the same configuration, sequential, on the same
merge request. Opus is two runs.

| Series | Model | Checklist | Sampling | Reasoning | Judge |
| --- | --- | --- | --- | --- | --- |
| `nemo/1` | nemotron-lightning-3p5-30b-a3b | `single`, 1 item | temp 0.3 | off (honoured) | two_stage |
| `nemo/3` | nemotron-lightning-3p5-30b-a3b | `grouped`, 3 items | temp 0.3 | off (honoured) | two_stage |
| `nemo/8` | nemotron-lightning-3p5-30b-a3b | `default`, 8 items | temp 0.3 | off (honoured) | two_stage |
| `muse/3` | muse-glimmer-30b | `grouped`, 3 items | temp 0.3 | provider default (≈high) | two_stage |
| `muse/3 low` | muse-glimmer-30b | `grouped`, 3 items | temp 0.3 | `reasoning_effort = "low"` | two_stage |
| `muse/3 low+s` | muse-glimmer-30b | `grouped`, 3 items | temp 1.0, top_p 0.95, top_k 64 | `low` | two_stage |
| `muse/8 low+s` | muse-glimmer-30b | `default`, 8 items | temp 1.0, top_p 0.95, top_k 64 | `low` | two_stage |
| `opus/1` | claude-opus-5 | `single`, 1 item | harness default | adaptive | **none** |

Common to all: `max_turns = 15`, `max_tokens = 8000`, reference pre-pass on,
scope gate ±5 lines, concurrency 4, same prompts, same annotated context.

Two settings deserve a note. `enable_thinking = false` is honoured by
nemotron-lightning and **ignored** by muse-glimmer, which reasons on every call
regardless; the working lever there is `reasoning_effort` in
`[reviewer.extra_body]`, where `low` and `medium` change depth and `none` does
not disable anything. And Opus ran without the judge, so its numbers are raw
finder output — compare them to the *raw* column, not the confirmed one.

## Recall against the full truth set

How many of the 31 known defects a series surfaced at least once across its runs.
One block is one defect.

```
                       ┌───────────────────────────────┐ 31
nemo/1                 │█                              │  1   3%
nemo/3                 │██                             │  2   6%
nemo/8                 │██                             │  2   6%
muse/3                 │████████████                   │ 12  39%
muse/3 low             │█████████████                  │ 13  42%
muse/3 low+s           │████████████                   │ 12  39%
muse/8 low+s           │███████████████                │ 15  48%
opus/1                 │██████████████████████         │ 22  71%
                       └───────────────────────────────┘
```

Per single run rather than per five, which is what a user actually gets:

```
                       ┌───────────────────────────────┐ 31
nemo/1                 │                               │  0.2   1%
nemo/3                 │                               │  0.4   1%
nemo/8                 │█                              │  1.2   4%
muse/3                 │███                            │  3.8  12%
muse/3 low             │█████                          │  5.0  16%
muse/3 low+s           │█████                          │  5.0  16%
muse/8 low+s           │███████                        │  7.4  24%
opus/1                 │█████████████████              │ 17.5  56%
                       └───────────────────────────────┘
```

## Recall split by what the defect needs

The truth set has two halves and they behave nothing alike. Thirteen defects are
caught by a build; eighteen need somebody to read the code.

```
                        caught by a build (13)        needs a reviewer (18)
                       ┌─────────────┐               ┌──────────────────┐
nemo/3                 │█            │  1   8%       │█                 │  1   6%
muse/3                 │███          │  3  23%       │█████████         │  9  50%
muse/3 low+s           │█            │  1   8%       │███████████       │ 11  61%
opus/1                 │████████████ │ 12  92%       │██████████        │ 10  56%
                       └─────────────┘               └──────────────────┘
```

Opus's lead is almost entirely the build half. On the half a reviewer is for,
muse-glimmer and Opus are close — with the caveat below that fifteen of those
eighteen entered the truth set from muse-glimmer's own output.

## False positives

Of everything a series shipped, how much was verified wrong. Solid blocks are
findings proven false against the code; light blocks are findings nobody has
adjudicated yet, so the true rate is somewhere inside the bar.

```
                       0%        10%       20%       30%
                       ├─────────┼─────────┼─────────┤
nemo/8                 │██░░░░░░                     │   4%…15%   2 false, 5 unlabelled of 48
muse/3                 │██████░░░░                   │  12%…20%   3 false, 2 unlabelled of 25
muse/8 low+s           │████░░░░░░░░░░░              │   8%…30%   6 false, 15 unlabelled of 71
opus/1                 │█░░░░░░░░░░                  │   2%…21%   1 false, 9 unlabelled of 48
```

Rate alone understates the problem. Of the nine false findings across the
muse-glimmer series, **six carried severity `blocker`**, and they cluster into
two persistent misreadings rather than scattering as noise:

| Claim | Why it is false |
| --- | --- |
| `SetupPasscodeView` instantiated without a required `controller:` | The struct has no explicit `init`; `weak var controller: T?` is an optional `var`, so the memberwise initialiser defaults it to nil. `swiftc -typecheck` on a minimal reproduction exits 0. |
| `NCMediaHost` passes a storyboard name where a view-controller identifier belongs | `NCMedia.storyboard` really does carry `storyboardIdentifier="NCMedia.storyboard"`. Oddly named, but it resolves. |
| Album name reaches a WebDAV path unescaped | Validation rejects `/` and `\`, so `..` cannot climb, and `encodedToUrl` percent-encodes the rest. |

All three are refuted by opening one file *outside the diff*. The judge has the
tools to do that and did not.

## What the judge removes

Its own verdict on the finder, not a measure of truth:

```
                       0%        25%       50%
                       ├─────────┼─────────┤
nemo/1                 │███████████████████       │ 49% rejected, 9 duplicates
nemo/3                 │█████████████████████     │ 54% rejected, 1 duplicate
nemo/8                 │█████████████             │ 34% rejected, 1 duplicate
muse/3                 │███████                   │ 17% rejected, 0 duplicates
muse/3 low             │███                       │  9% rejected, 0 duplicates
muse/3 low+s           │███                       │  8% rejected, 1 duplicate
muse/8 low+s           │██                        │  6% rejected, 6 duplicates
```

The judge earns its cost on a noisy finder and idles on a quieter one. It is
also where the six false blockers got through, so a low rejection rate is not
by itself good news.

## Cost

Per run, averaged over the series.

| Series | Turns | Wall | Reviewer prompt | Judge prompt | Verified findings |
| --- | --- | --- | --- | --- | --- |
| `nemo/3` | 8.0 | 65 s | 0.6M | 5.3M | — |
| `nemo/8` | 42.2 | 94 s | 2.8M | 7.8M | 8.2 |
| `muse/3` | 36.0 | 299 s | 2.2M | 2.2M | 4.0 |
| `muse/3 low` | 12.2 | 121 s | 0.7M | 2.1M | 5.0 |
| `muse/3 low+s` | 14.0 | 99 s | 0.8M | 2.1M | 5.0 |
| `muse/8 low+s` | 35.0 | 181 s (median) | 2.0M | 4.3M | 10.0 |
| `opus/1` | 40 tool calls | 648 s | — | none | 19.0 |

Prompt tokens are 82–99% cache hits, so they are a size signal, not a bill.

## What moved the numbers and what did not

**Moved it.** The model. Swapping nemotron-lightning for muse-glimmer took
per-run recall from 1–4% to 12–16%, and per-run verified findings from ~8
one-line "no tests" entries to a spread of real defects.

**Moved it a little.** Checklist count, but only on the better model. For
muse-glimmer, eight items doubled verified findings per run (5.0 → 10.0) at the
same false-positive rate and 2.5× the tokens. For nemotron-lightning the same
change bought nothing: 46 of its 74 findings were one "no tests" entry per file.

**Did not move it.** Reasoning depth and sampling. `reasoning_effort = "low"`
cut cost by two thirds and lost one blocker; temperature 1.0 with top-p 0.95 and
top-k 64 tightened the run-to-run spread and left recall where it was.

**Never moved.** Recall on the independent half of the truth set sits at 6–12%
for every roboviewer configuration measured. The class of defect it misses —
a symbol that resolves nowhere — is already computed by the reference pre-pass
and printed into the prompt; the models do not act on it.

## How to read these numbers

- **One merge request.** Everything here is `n = 1` at the repository level.
  A second merge request may reorder the table.
- **Half the truth set is not independent.** Sixteen entries were established by
  hand before any of these runs; fifteen were added by verifying muse-glimmer's
  own output against the code. Any series compared on the second half is
  compared on ground it helped define — `origin` in the reference marks which is
  which, and the honest cross-model comparison uses the sixteen.
- **Unlabelled findings are not false.** Between 2 and 15 findings per series
  have not been adjudicated. The false-positive bars show that as a range rather
  than pretending the lower bound is the answer.
- **Opus ran without a judge.** Its output is raw finder output. It is here as
  a ceiling to measure against, not as a configuration of this tool.
- **The yardstick is defects, not reviewer comments.** Every entry in the truth
  set was established against the code and carries the evidence for it. None of
  it comes from what a human reviewer happened to write on the merge request,
  and no number here is an overlap-with-humans score — that would measure
  similarity to a reviewer rather than correctness, and the two miss different
  things.
- **Severity is not stable.** The same defect comes back as `blocker` in one run
  and `minor` in the next — 1 of 7 repeated findings kept its severity on
  muse-glimmer, against 15 of 17 on Opus. Do not gate a pipeline on it yet.
