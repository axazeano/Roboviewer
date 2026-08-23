# The benchmark

How well the reviewer does is measured on real merge requests: a fixed list of
them, on disk at the commits reviewers were looking at, and the tool run over
the whole list with one command. This page is the `benchmark` command — the
index, the clones, the runs — and how it is rebuilt on a machine that has never
seen it.

Roboviewer itself reads two git branches and never talks to a forge. Fetching a
pull request is therefore a step *beside* the review: the benchmark is the one
part of the package that reaches GitHub, and nothing on the review path imports
it.

```
benchmark list add <pull-request-url>   record it in the index and clone it
benchmark list show                     the index, and what of it is on disk
benchmark list remove <id|url>          take it out of the index
benchmark run [roboviewer flags]        review every entry with the tool
benchmark fetch                         clone what is listed and not yet there
benchmark search <query>                find candidates on GitHub
```

Everything lives under one directory, `benchmarks/` where the command is run:

```
benchmarks/
  items.toml               the index: one [[entry]] per merge request — committed
  references/<id>.toml     what a good review of that entry finds — committed
  repos/<id>/              the clone, checked out at the head reviewers saw
  comments/<id>.json       the review threads: file, line, author, body, resolved
  runs/<stamp>/            one `benchmark run`: summary.json, summary.md, <id>/<run_id>/
```

`--root PATH` or `$ROBOVIEWER_BENCHMARKS` points it elsewhere. The index and the
references are the benchmark; the rest is a cache of real repositories, expected
to be gigabytes, ignored by git and deletable at any time — the index is what
brings it back.

## Adding a merge request

```bash
benchmark list add https://github.com/prometheus/prometheus/pull/19339
```

```
✔ prometheus-19339         added to benchmarks/items.toml
  base e75af386be14 → head bfbc9d9295f7, 3 thread(s)
  `domain` and `found` are blank; fill them in before committing the index.
✔ prometheus-19339         4 review comment(s) in 3 thread(s)
Review it: roboviewer e75af386be14 bfbc9d9295f7 -C benchmarks/repos/prometheus-19339
```

Two requests to GitHub: the pull request, for the base, the size, the language
and the licence; and its review threads, for the head. Then the clone. The entry
is appended to `items.toml` as text, so whatever you wrote around the other
entries stays; `--no-fetch` writes the entry and leaves the clone to `fetch`.

`found` and `domain` are left blank on purpose. They are the two fields a later
reader uses to decide whether an entry earns its place — see
[the selection criteria](benchmark-selection.md) — and a sentence generated
from a title would read exactly like one somebody had checked.

`list remove prometheus-19339` takes the entry out and leaves the clone for you
to delete; `list show` prints the index with a mark per entry for whether it
is on disk.

## Running the benchmark

```bash
benchmark run                       # every entry, the tool's defaults
benchmark run --no-judge -v         # the flags are roboviewer's own
benchmark run --entries cli-13946   # one entry
benchmark run --refresh             # fetch again first
```

One `roboviewer <base> <head> -C benchmarks/repos/<id>` per entry, with
whatever flags follow passed through unchanged — `--config`, `--checklist`,
`--format`, `--language`, `--no-judge`, `-j`, `-v` all mean what they mean on
the tool, and there is no second set of flags to keep in step. Two of the
tool's are not for here: `-C`, because each entry is reviewed in its own
clone, and `--output`, which works but puts every entry's reports where you
said instead of under the run. An entry that is not on disk is fetched first;
one that cannot be is reported and the others still run.

```
▸ 12 entr(ies) → benchmarks/runs/2026-08-23-141502
── cli-13946  https://github.com/cli/cli/pull/13946
▸ Reviewing feature/worktree → trunk (2 files) ...
✔ cli-13946                7 finding(s), 4 confirmed, 1 out of scope, 312s → benchmarks/runs/2026-08-23-141502/cli-13946/20260823-141502-a1b2
...

11 reviewed, 1 not: 63 finding(s), 38 confirmed
Not reviewed: envoy-46545
Summary: benchmarks/runs/2026-08-23-141502/summary.json
```

One run is one directory under `benchmarks/runs/`, named for the minute it
started. The tool's own output for each entry is underneath — `run.json`,
`findings.json`, the reports — and beside them `summary.json`, one row per
entry: status, exit code, run id, model, findings, confirmed, out of scope,
tokens, seconds, the directory; and `summary.md`, the same as a table. The
summary is what two runs are compared on; the run directories are the sizable
raw material and are not meant for git.

The command exits 0 when every entry was reviewed — the tool's own 0 and 1
both count as a finished review — and 1 otherwise; 2 when it could not start.

## What an entry says

```toml
[[entry]]
id = "prometheus-19339"
url = "https://github.com/prometheus/prometheus/pull/19339"
base = "e75af386be14c1229b3976d4b7adfff853a4022e"
head = "bfbc9d9295f7a9f1e32f14428f1f36e49319a5d9"

language = "Go"
domain = "time-series database"
found = "A reviewer found the added lock guard cannot fix the hang it is for."
license = "Apache-2.0"
files = 1
added = 17
removed = 11
```

[`items.example.toml`](../benchmarks/items.example.toml) is the same format
with every field explained and deliberately unbuildable SHAs.

| Field | Read by | Meaning |
| --- | --- | --- |
| `id` | the fetcher | Names the clone under `repos/`. Keep it stable — renaming rebuilds from scratch. |
| `url` | the fetcher | The pull request. GitHub only; owner, repository and number are read out of it. |
| `base` | the fetcher | The commit on the target branch. Full 40-character SHA. |
| `head` | the fetcher | **The commit reviewers saw**, not the merged head. Full SHA. |
| `language`, `domain` | people | So the benchmark can be checked for leaning on one stack or one kind of program. |
| `found` | people | One line on what the review found, so a later reader can judge the entry. |
| `license` | people | That the repository may be cloned and kept locally for measurement. |
| `files`, `added`, `removed` | people | Diff size, so the list spans small and large changes. |

The clone holds both commits, so reviewing one by hand is one command:

```bash
roboviewer <base> <head> -C benchmarks/repos/<id>
```

`base` is the target side and `head` the source side, exactly as in a normal
run. Roboviewer diffs from the branch point of the two, not from `base`
directly, so commits that landed on the target branch after the pull request was
opened stay out of the review — the same thing a reviewer on the pull request
page was looking at.

The head is the field to get right and the easy one to get wrong. Take the
merged head and the defects reviewers found are already fixed in it, so every
hit is impossible and the benchmark measures nothing. Anything the fetcher can
check it does: a branch name or a short SHA is refused, and two entries cannot
claim one directory.

The branch tip is the same trap wearing a different hat, and it is the one the
API leads you into: `head.sha` on a pull request is the last commit of the
branch, which on any review the author responded to is the commit *after* the
fixes. `list add` therefore takes the head from the review threads — the commit
the earliest one was written against — and falls back to the tip only when no
thread names a commit, saying so. Each review comment records the commit it was
written against, so the fetcher compares the two and says so when they disagree:

```
✔ redis-9954              16 review comment(s) in 8 thread(s)
  The review was written against 6a6f58b14f6c, not the head e6d1b1dff6db this
  entry names. Whatever reviewers asked for is already fixed at this head, so
  the entry measures nothing — unless a later round is what you meant.
```

It is a warning rather than a refusal: comments land on several commits when a
branch is pushed to twice, and pointing an entry at the last round is a choice.
Nothing is said when the head is among the commits reviewers commented on. The
commit each thread belongs to is saved in `comments/<id>.json`, so an entry
built earlier can be checked without asking GitHub again — after one
`--refresh`, which is what fills the field in.

## The references

`references/<id>.toml` is what a good review of that entry finds, written by a
person who checked every claim against the code at the entry's head. One
`[[finding]]` per claim, with a verdict: `"expected"` for a defect the review
has to find, `"false"` for a claim the review once produced that was checked
and refuted, with `why_false` saying why. Both halves are needed — without the
false ones, high recall can be bought with noise.

```toml
[[finding]]
id = "copy-ocid-source-path"
verdict = "expected"
origin = "verified-from-run"
severity = "major"
kind = "correctness"
file = "iOSClient/Albums/Presentation/Details/AlbumDetailsViewModel.swift"
line = 299
what = "sourcePath is built from an ocId, so the COPY goes out with the wrong path"
evidence = "AlbumDetailsViewModel.swift:294-302 — a line above, photo is passed to getMetadataFromOcId"
```

Every finding carries in `evidence` exactly what it was checked with; one
without evidence is an opinion and does not go in. `origin` says where the
claim came from — `manual`, collected by reading the code independently of any
run, or `verified-from-run`, taken from a run's output and then verified — so
a model is not compared on ground it helped define. There is one reference so
far, [`ios-4091`](../benchmarks/references/ios-4091.toml), the merge request
[the measurements](measurements.md) were made on. Scoring a run against a
reference is TASK-20; the loader in `roboviewer.benchmark.references` holds
the shape until then.

## Finding candidates

Adding an entry starts with a pull request of the right size whose review found
something, and GitHub cannot search for the first half: there is no `files:`
qualifier and no `additions:`. The size does come back with the results through
GraphQL, so the filter runs on this side — one request per fifty candidates
instead of one per candidate.

```bash
benchmark search "is:pr is:merged language:Go review:changes_requested" \
  --min-files 30 --min-stars 500
```

```
▸ 41742 match the query, 250 read, 12 pass the filters
   171 dropped — too few files
    42 dropped — nobody reviewed a line
    25 dropped — too few stars
   410 files    3892+/1974  -  18 threads     2636 ★  MIT          .../compozy/pull/440
   103 files    6523+/243   -  31 threads     7858 ★  Apache-2.0   .../filebrowser/pull/2806
```

**`review:changes_requested` is doing most of the work there, and without it the
command returns nothing.** On a bare `is:pr is:merged` search about 98 per cent
of what comes back has no review thread at all — merged bot updates and solo
merges — so every other filter is applied to pull requests that were never going
to qualify. With the qualifier the reviewed share goes to roughly 80 per cent.
`comments:>5` works too and less well; sorting is not an option, since GitHub
times out sorting a result set this large.

Every rejection is counted by reason, one reason per candidate in the order the
filters ask, so the numbers add up to what was dropped and read as a funnel. When
nothing passes, the dominant reason is named along with the fix — a bare count of
zero looks like GitHub has run out of candidates, when it usually means the query
asked for the wrong thing.

`--heads` adds the commit reviewers were looking at, and what they said at it:

```
── cluster-api-14069  https://github.com/kubernetes-sigs/cluster-api/pull/14069
   base 1e0c0efc3f13 → head 9e471f2dadcc (the commit reviewers saw)
   · Makefile:386 @sbueringer: I think this line should not be changed
   · test/infrastructure/docker/main.go:438 @sbueringer: Let's also drop the now redundant …
```

That head is the commit the earliest review thread was written against, not the
merged head — at the merged head everything reviewers found is already fixed and
the entry would measure nothing.

Sometimes no head comes back. That is an answer, not a gap: the search asks
GraphQL to resolve the commit a thread was written against rather than to repeat
the SHA it stored, so nothing comes back when GitHub can no longer reach it —
force-pushed and gone. An entry naming that commit could never be rebuilt, which
is one of the things `benchmark-selection.md` disqualifies, and the command says so
instead of suggesting the SHA be found by hand. `--toml` prints the same thing as
an `[[entry]]` block ready to paste into the index — or skip the paste and
`list add` the URL.

**It does not decide whether a review found a defect.** That judgement is what
[the selection criteria](benchmark-selection.md) are for, and no query expresses
it — a review made of naming notes has the same thread count as one that caught
a race. The threads are printed so the call can be made without opening the pull
request, and it stays a person's call.

Two limits worth knowing. Pages are fifty because a hundred nodes of this shape
502s intermittently — a hundred comes back most of the time and then fails twice
in a row under load, which is worse than a limit that always refuses. And search
never returns more than 1000 results however far the cursor is walked: when a run
hits that, it says so, and the fix is a narrower query — usually a shorter
`created:` window — rather than more pages.

**Licences are allowed, not refused.** Only the ones on a listed set survive —
MIT, Apache-2.0, the BSD family, ISC, and the copyleft licences, which are fine
here because every entry is read locally and nothing is redistributed. Everything
else is dropped, and the count is printed rather than swallowed.

The list is written this way round because the two directions are not
symmetric. What is safe is a closed set that changes about once a decade; what is
unsafe is open and keeps growing, and a refused list is out of date the day after
it is written. It also disposes of GitHub's two ways of saying it does not know —
no `licenseInfo` when it found no file, `NOASSERTION` when it found one it could
not map — without having to guess what a file called `Лицензия.md` or
`old_license.md` contains.

Being dropped is not a claim that a repository is unlicensed. `juju/juju` spells
the file `LICENCE`, carries AGPL-3.0 in full, and comes back as `NOASSERTION`.
It is a claim that nobody has read it — and an entry records a licence, so a
recorded value nobody read is a claim nobody checked. `--any-license` stands the
list down for a candidate somebody has decided to read the repository for.

One name is worth knowing: `BSL-1.0` is the Boost Software Licence, permissive
and on the list; `BUSL-1.1` is the Business Source Licence, source-available and
not. Source-available terms are the ones that actually bite here, because they
restrict use rather than distribution.

A token is required here, unlike the rest of the command: the search API is
GraphQL, and GraphQL refuses anonymous requests. It comes from the same three
places as everywhere else — see [the token](#the-token).

## Fetching and rebuilding

```bash
benchmark fetch                          # clone what is listed and not yet there
benchmark fetch --entries django-17421   # one entry
benchmark fetch --refresh                # fetch again anyway
```

`fetch` is what `list add` and `run` do on their own when a clone is missing;
on its own it warms the cache — before a run on a machine that has never seen
the benchmark, or in a CI cache step. It does nothing to an entry that is
already built. "Built" means all three of: the marker inside the clone's
`.git/` records this url, base and head; both commits are in the clone; the
comments are on disk. Checking that costs no network at all, so a rerun over a
finished benchmark is instant and works offline.

`--refresh` reuses the clone that is already there — it costs one fetch, not
another copy of the repository's history. Editing an entry's `base` or `head` in
the index has the same effect without the flag: the marker no longer matches, so
the entry is built again. Deleting `repos/<id>` by hand has the same effect too:
the marker goes with it.

## The token

```bash
gh auth login          # or: export GITHUB_TOKEN=ghp_...
```

Read from `GITHUB_TOKEN`, then `GH_TOKEN`, then from whatever `gh auth login` is
holding — a machine already set up for the GitHub CLI needs nothing further, and
its token stays in the keyring rather than being copied into a shell profile. An
explicit variable wins where both exist, because setting one is a deliberate
choice: a CI job, or a second account. Worth having for two reasons.
Anonymous requests are capped at sixty an hour, which a benchmark of a dozen
entries can reach. And thread resolution exists only in GitHub's GraphQL API,
which never answers without a token: fetched anonymously, every thread is saved
with `"resolved": null` and the file says `"resolution": "unknown"` so nobody
mistakes it for "nothing was resolved". Set a token and `--refresh` to fill that
in.

A read-only token with no scopes is enough for public repositories.

When the rate limit is what stopped the command, it says so, says when the limit
resets, and stops rather than asking for the same refusal once per entry.
Whatever was built before that point is kept.

## When an entry fails

The command reports the entry, names what is missing and carries on with the
rest, exiting 1 at the end. The half-built clone is never left behind:
`repos/<id>` either holds a complete clone or does not exist.

The usual failure is a head that GitHub no longer has. Pull request branches get
force-pushed, and the commit reviewers were looking at can disappear with the
old branch tip. The fetcher asks for the SHAs directly first and falls back to
`refs/pull/<n>/head`, which GitHub keeps after the branch is deleted; when
neither has it, the entry needs a different head — or a different pull request.

## Tests

`tests/test_benchmark_*.py` run under `pytest` with the rest of the suite and
never touch the network: the origins are local repositories, every HTTP request
goes through an injected transport, and `benchmark run` is driven against a
stand-in for the tool's `main`.
