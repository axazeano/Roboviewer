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
export GITHUB_TOKEN=ghp_...
```

Read from `GITHUB_TOKEN` or `GH_TOKEN`, and worth setting for two reasons.
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

`tests/test_corpus_list.py`, `tests/test_corpus_fetch.py` and
`tests/test_corpus_github.py` run under `pytest` with the rest of the suite and
never touch the network: the origins are local repositories, and every HTTP
request goes through an injected transport.
