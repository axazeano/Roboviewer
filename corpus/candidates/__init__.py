"""Candidates for the corpus, and the part of choosing one a machine can do.

Growing the corpus means finding pull requests of a certain size whose review
found something, and GitHub search cannot express the first half: there is no
`files:` qualifier and no `additions:`. What search does return, through
GraphQL, is `changedFiles` on every pull request it yields — so the filter runs
on this side, at one request per page instead of one per candidate.

Three parts, and they are separate because they fail and change for different
reasons:

- `candidate` — the pull request a search yielded, as facts. The word the other
  three are about, so it belongs to none of them.
- `criteria` — the bar one is held to, and what to say when it is missed. No
  client, no request, nothing that can fail, and true whoever is asked.
- `on_github` — the two questions only GitHub can answer: which pull requests
  match, and which commit their reviewers were looking at. Named for the forge
  because a different forge is what replaces it, and both questions go at once.
- `entry_toml` — the half-written `[[entry]]` to paste, which outlives both.

What is deliberately absent is the judgement `docs/corpus-selection.md` asks
for: whether a thread found a defect or a naming preference. That decides
whether an entry earns its place, no query expresses it, and a command that
guessed at it would fill the corpus with reviews about whitespace. The threads
come back with the head so a person can read them and decide.
"""
