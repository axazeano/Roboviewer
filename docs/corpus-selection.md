# Choosing what the baseline is measured on

The corpus decides what the baseline number means. A list assembled by taste
would make every later number an argument about the list, so the bar is written
down first and each entry is admitted against it. Adding one later is a matter
of checking, not of persuading.

The list itself is [`corpus.toml`](../corpus.toml); how it is built into
directories is [corpus.md](corpus.md).

## What qualifies

1. **A public GitHub pull request.** The repository clones anonymously, and the
   licence allows keeping a local copy for measurement.
2. **The review found at least one defect** — something that would misbehave,
   break, or bite later: a wrong result, a race, a swallowed error, a leak, a
   missing case, an unbounded cost, a broken contract. Written down in one line
   in the entry.
3. **The commit reviewers saw is identifiable**: a review comment carries an
   `original_commit_id`, and GitHub still has that commit.
4. **The defect-finding comments are anchored to a file and a line**, so a run
   can be scored against them.
5. **Someone other than the author found it.** A pull request where the author
   reviews their own work measures nothing.

## What disqualifies

- **A review made of preferences.** Naming, formatting, import order, "I'd use a
  comprehension here" — however long the thread. A long review is not a review
  that found something.
- **A review made only by bots.** Several of the repositories looked at now have
  an AI reviewer commenting on every pull request, and some of those comments are
  good. They still cannot be the truth an AI reviewer is measured against: the
  measurement would be of agreement with another model, not of finding defects.
  A human finding stands whether or not a bot found it too.
- **Nothing for a reviewer to find**: reverts, version bumps, generated-code
  updates, pure renames.
- **A defect only findable from outside the repository** — an internal incident,
  a customer report, a private specification. Roboviewer reads two branches; a
  finding it could not reach is not a miss worth counting.
- **A reviewed head GitHub no longer has.** Force-pushed and unreachable, even
  through `refs/pull/<n>/head`, means the entry cannot be rebuilt.
- **Anything requiring authentication** to clone or to read.

## Which commit is the head

This is the decision the whole measurement turns on. Measure the merged head and
the defects reviewers found are already fixed in it: every hit is impossible and
the baseline reads zero for reasons that have nothing to do with the tool.

The rule used here: **`head` is the commit the earliest defect-finding review
comment was made on**. Not the earliest comment of any kind — a first round that
produced only naming notes says nothing about whether a defect was present — and
not the last, which usually already carries the fixes. Where the review ran over
several rounds, later rounds may concern code that did not exist at the chosen
head; those findings are not this entry's truth.

`base` is the pull request's own `base.sha`. Roboviewer diffs from the branch
point of the two commits rather than from `base` directly, so a `base` that has
moved on since the branch was cut changes nothing — which is why the recorded
diff sizes are measured the same way, from the branch point.

## The frame

Per-entry criteria are not enough: a list of ten entries that all pass can still
measure one stack, one team's habits, or one size of change. So, over the list
as a whole:

- **At least three languages, and more than one domain.** The stated direction
  is a general-purpose tool; a Swift-only corpus would measure iOS tuning.
- **No more than three entries from one repository**, so no single project's
  review culture sets the tone.
- **Sizes spread rather than clustered**: small changes and large ones, because
  a reviewer that does well on a one-file diff and drowns in a fifteen-file one
  is a different tool from one that does the reverse.

`tests/test_corpus_list.py` checks these against the committed file, so the
frame is enforced rather than remembered.

## The list as it stands

Eleven entries, four languages, eight domains, from 28 to 1703 changed lines.

| Entry | Language | Domain | Size at the reviewed head | Licence |
| --- | --- | --- | --- | --- |
| `cli-13946` | Go | developer CLI | 2 files, +643/−25 | MIT |
| `cli-14007` | Go | developer CLI | 5 files, +467/−3 | MIT |
| `prometheus-18091` | Go | monitoring | 5 files, +124/−4 | Apache-2.0 |
| `prometheus-19339` | Go | time-series database | 1 file, +17/−11 | Apache-2.0 |
| `kafka-22975` | Java | stream processing | 7 files, +248/−173 | Apache-2.0 |
| `kafka-23093` | Java | stream processing | 6 files, +355/−66 | Apache-2.0 |
| `kafka-22969` | Java | message broker | 2 files, +69/−2 | Apache-2.0 |
| `envoy-46545` | C++ | service proxy | 8 files, +198/−15 | Apache-2.0 |
| `ansible-87361` | Python | IT automation | 12 files, +90/−28 | GPL-3.0 |
| `ansible-87266` | Python | IT automation | 3 files, +77/−4 | GPL-3.0 |
| `home-assistant-178506` | Python | home automation | 15 files, +1575/−128 | Apache-2.0 |

What each review found is one line per entry in `corpus.toml`.

**On the licences.** Every entry is cloned and read locally and nothing derived
from it is distributed, which is why GPL-3.0 sits here beside MIT and Apache-2.0
without difficulty: copyleft constrains distribution, and there is none. What
would disqualify a repository is a licence restricting use or copying at all —
source-available terms such as BUSL or SSPL. `grafana` and `terraform` were
passed over for that reason rather than for anything about their reviews.
`corpus find` applies this the other way round, admitting only the licences on a
listed set, because what is safe is a closed set and what is unsafe keeps
growing.

## What was looked at and turned down

The bar is only meaningful if it rejects things. Candidates that reached a full
reading of their review threads and did not get in:

| Candidate | Why not |
| --- | --- |
| `python/cpython#143305` | The pull request *fixes* a use-after-free; the review itself argued about whether the bug report was valid and how to write the test. A review that debates a fix is not a review that found a defect. |
| `tokio-rs/tokio#8269` | Test hygiene throughout — a sleep that is too long, a `#[should_panic]` without a message. Real improvements, no defect. |
| `valkey-io/valkey#4356`, `#4359` | Findings are entirely from `coderabbitai` and a review bot. Excluded by the bots rule. |
| `microsoft/vscode#329734` | Same: the substantive findings are Copilot's, confirmed by the author. |
| `rust-lang/cargo#17300`, `#17067` | Commit structure and test-shape discussion between maintainers. |
| `django/django#21636`, `#21596` | Documentation wording and where a module should live. |
| `rails/rails#58060`, `#58281` | API design for Ractor support — a real argument, but about design, not about a defect present in the diff. |

The bots rule is what cost the most: on the repositories surveyed, a growing
share of line-anchored, defect-shaped review comments now come from an AI
reviewer rather than a person. Rust and TypeScript entries were the casualties —
the human reviews found in those repositories in this pass were design
discussions, and the defect-finding was the bot's. The list is four languages
rather than six for that reason, and worth revisiting when the corpus grows.

## The three iOS merge requests

Working notes hold three iOS merge requests with hand-recorded truth, and they
remain useful. They are **not** in this list: two are private company
repositories and one is a personal mirror, so none of them can be rebuilt by
anyone else, which is the property this file exists to guarantee. They stay a
separate sample, scored separately, and they are a sample rather than the frame —
a Swift-only measurement would say how well the tool is tuned for iOS, not how
well it reviews.

## Adding an entry

1. Search for candidates with `python -m corpus find` — see
   [finding candidates](corpus.md#finding-candidates). It drops what no human
   reviewed, what is too small to measure and what carries a licence nobody has
   read, and prints the review threads that survive, so the next step rarely
   needs the pull request opened.
2. Decide against the criteria above. This is the part no query does: a review
   made of naming notes has the same thread count as one that caught a race, and
   only reading the threads tells them apart.
3. Take the fields with `--heads --toml`, which prints the `[[entry]]` ready to
   paste: `base` is the pull request's `base.sha`, `head` the commit the earliest
   review thread was written against, and the diff size is measured at that head
   rather than at the merged one. Confirm the head against the thread that found
   the defect — the earliest thread is the tool's proposal, and a first round of
   naming notes would make it the wrong commit.
4. Write the one line saying what the review found, fill in `domain`, and run
   `pytest tests/test_corpus_list.py` — it will refuse a list that has drifted
   out of the frame.

A token is needed throughout, and the search step will not run without one at
all: it is GraphQL, and GraphQL refuses anonymous requests. `gh auth login` is
enough — see [the token](corpus.md#the-token).
