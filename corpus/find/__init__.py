"""Candidates for the corpus, and the part of choosing one a machine can do.

Growing the corpus means finding pull requests of a certain size whose review
found something, and GitHub search cannot express the first half: there is no
`files:` qualifier and no `additions:`. What search does return, through
GraphQL, is `changedFiles` on every pull request it yields — so the filter runs
on this side, at one request per page instead of one per candidate.

Three parts, and they are separate because they fail and change for different
reasons:

- `criteria` — what a candidate is and what disqualifies one. No client, no
  request, nothing that can fail; a statement about a pull request rather than
  a way of getting one.
- `search` — walking GitHub's search API, with the two limits it imposes.
- `proposal` — what to put in front of a person once a candidate survives: the
  commit reviewers were looking at, and the entry stanza to paste.

What is deliberately absent is the judgement `docs/corpus-selection.md` asks
for: whether a thread found a defect or a naming preference. That decides
whether an entry earns its place, no query expresses it, and a command that
guessed at it would fill the corpus with reviews about whitespace. The threads
come back with the head so a person can read them and decide.
"""
