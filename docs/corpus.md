# Building the corpus

The baseline says how much of what human reviewers found on real merge requests
Roboviewer finds too. That needs those merge requests on disk, positioned at the
commits reviewers were looking at, with what they said saved beside them. This
page is how that directory gets built, and rebuilt on a machine that has never
seen it.

Roboviewer itself reads two git branches and never talks to a forge. Fetching a
pull request is therefore a step *beside* the tool: a separate command, in a
package that is not part of the wheel.

```bash
python -m corpus corpus.toml
```

Run it from the repository root: the package ships with the source and is
deliberately not installed, so nothing on the review path can reach a forge.

```
▸ 12 entr(ies) → /Users/you/.cache/roboviewer/corpus
• requests-6800            already built
✔ django-17421             22 review comment(s) in 7 thread(s)
✗ flask-5100               https://github.com/pallets/flask.git did not yield 9f2c…

2 built, 1 already there, 1 failed
Failed: flask-5100
Review one: roboviewer 4f1c8a2b91de 77b0c9de41aa -C /Users/you/.cache/roboviewer/corpus/django-17421/repo
```

## What one entry becomes

```
<corpus>/<id>/repo/            the clone, checked out at the head reviewers saw
<corpus>/<id>/comments.json    the review threads: file, line, author, body, resolved
<corpus>/<id>/corpus.json      what this was built from, and when
```

The clone holds both commits, so the review is one command:

```bash
roboviewer <base> <head> -C <corpus>/<id>/repo
```

`base` is the target side and `head` the source side, exactly as in a normal
run. Roboviewer diffs from the branch point of the two, not from `base`
directly, so commits that landed on the target branch after the pull request was
opened stay out of the review — the same thing a reviewer on the pull request
page was looking at.

## The list

One committed TOML file describes the corpus. It is the whole input: delete the
clones, run the command again, and the same corpus comes back. Nobody has to
remember which commit was the right head.

```toml
[[entry]]
id = "requests-6800"
url = "https://github.com/psf/requests/pull/6800"
base = "1111111111111111111111111111111111111111"
head = "2222222222222222222222222222222222222222"

language = "Python"
domain = "HTTP client library"
found = "Retries reused a consumed request body, so the second attempt sent nothing."
license = "Apache-2.0"
files = 3
added = 74
removed = 12
```

`corpus.example.toml` in the repository root is this file with the fields
explained.

| Field | Read by | Meaning |
| --- | --- | --- |
| `id` | the fetcher | Names the directory. Keep it stable — renaming rebuilds from scratch. |
| `url` | the fetcher | The pull request. GitHub only; owner, repository and number are read out of it. |
| `base` | the fetcher | The commit on the target branch. Full 40-character SHA. |
| `head` | the fetcher | **The commit reviewers saw**, not the merged head. Full SHA. |
| `language`, `domain` | people | So the corpus can be checked for leaning on one stack or one kind of program. |
| `found` | people | One line on what the review found, so a later reader can judge the entry. |
| `license` | people | That the repository may be cloned and kept locally for measurement. |
| `files`, `added`, `removed` | people | Diff size, so the list spans small and large changes. |

The head is the field to get right and the easy one to get wrong. Take the
merged head and the defects reviewers found are already fixed in it, so every
hit is impossible and the baseline measures nothing. Anything the fetcher can
check it does: a branch name or a short SHA is refused, and two entries cannot
claim one directory.

The branch tip is the same trap wearing a different hat, and it is the one the
API leads you into: `head.sha` on a pull request is the last commit of the
branch, which on any review the author responded to is the commit *after* the
fixes. Each review comment records the commit it was written against, so the
fetcher compares the two and says so when they disagree:

```
✔ redis-9954              16 review comment(s) in 8 thread(s)
  The review was written against 6a6f58b14f6c, not the head e6d1b1dff6db this
  entry names. Whatever reviewers asked for is already fixed at this head, so
  the entry measures nothing — unless a later round is what you meant.
```

It is a warning rather than a refusal: comments land on several commits when a
branch is pushed to twice, and pointing an entry at the last round is a choice.
Nothing is said when the head is among the commits reviewers commented on. The
commit each thread belongs to is saved in `comments.json`, so an entry built
earlier can be checked without asking GitHub again — after one `--refresh`,
which is what fills the field in.

## Finding candidates

Adding an entry starts with a pull request of the right size whose review found
something, and GitHub cannot search for the first half: there is no `files:`
qualifier and no `additions:`. The size does come back with the results through
GraphQL, so the filter runs on this side — one request per fifty candidates
instead of one per candidate.

```bash
python -m corpus find "is:pr is:merged language:Go review:changes_requested" \
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
zero looks like the corpus has run out of GitHub, when it usually means the query
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
the entry would measure nothing. `--toml` prints the same thing as an `[[entry]]`
stanza ready to paste into the list.

**It does not decide whether a review found a defect.** That judgement is what
[the selection criteria](corpus-selection.md) are for, and no query expresses
it — a review made of naming notes has the same thread count as one that caught
a race. The threads are printed so the call can be made without opening the pull
request, and it stays a person's call.

Two limits worth knowing. Pages are fifty, because GitHub answers fifty nodes of
this shape and refuses a hundred with a 502. And search never returns more than
1000 results however far the cursor is walked: when a run hits that, it says so,
and the fix is a narrower query — usually a shorter `created:` window — rather
than more pages.

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

## Where the corpus lives

In order:

1. `--corpus PATH`
2. `$ROBOVIEWER_CORPUS`
3. `~/.cache/roboviewer/corpus` (or `$XDG_CACHE_HOME/roboviewer/corpus`)

The default is outside every repository, including this one. A corpus inside a
repository under measurement would be diffed, indexed and eventually committed
by accident.

Real repositories are large; this directory is expected to be gigabytes. It is
a cache and can be deleted at any time — that is what the list is for.

## Rebuilding

A rerun does nothing to an entry that is already built. "Built" means all three
of: the marker records this url, base and head; both commits are in the clone;
the comments are on disk. Checking that costs no network at all, so a rerun over
a finished corpus is instant and works offline.

```bash
python -m corpus corpus.toml                     # fill in what is missing
python -m corpus corpus.toml --only django-17421 # one entry
python -m corpus corpus.toml --refresh           # fetch again anyway
```

`--refresh` reuses the clone that is already there — it costs one fetch, not
another copy of the repository's history. Editing an entry's `base` or `head` in
the list has the same effect without the flag: the marker no longer matches, so
the entry is built again.

## The token

```bash
gh auth login          # or: export GITHUB_TOKEN=ghp_...
```

Read from `GITHUB_TOKEN`, then `GH_TOKEN`, then from whatever `gh auth login` is
holding — a machine already set up for the GitHub CLI needs nothing further, and
its token stays in the keyring rather than being copied into a shell profile. An
explicit variable wins where both exist, because setting one is a deliberate
choice: a CI job, or a second account. Worth having for two reasons.
Anonymous requests are capped at sixty an hour, which a corpus of a dozen
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
rest, exiting 1 at the end. The half-built directory is never left behind:
`<corpus>/<id>` either holds a complete entry or does not exist.

The usual failure is a head that GitHub no longer has. Pull request branches get
force-pushed, and the commit reviewers were looking at can disappear with the
old branch tip. The fetcher asks for the SHAs directly first and falls back to
`refs/pull/<n>/head`, which GitHub keeps after the branch is deleted; when
neither has it, the entry needs a different head — or a different pull request.

## Tests

`tests/test_corpus_list.py`, `tests/test_corpus_fetch.py`,
`tests/test_corpus_github.py` and `tests/test_corpus_find.py` run under `pytest`
with the rest of the suite and
never touch the network: the origins are local repositories, and every HTTP
request goes through an injected transport.
