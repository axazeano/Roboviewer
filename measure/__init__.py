"""Instruments for measuring the tool, beside the tool.

Roboviewer reviews two branches and keeps nothing of how it got there. Finding
out how well it does needs two things it deliberately does not carry: something
to measure it on, and a way to watch what a run did rather than what it
concluded. Both live here, outside the wheel — `packages.find` in
pyproject.toml includes `roboviewer*` only — so nothing on the review path can
grow a dependency on a forge or on an instrument by accident.

    corpus/      builds the corpus of real pull requests the baseline is
                 measured on: `python -m measure.corpus measure/corpus.toml`
    trace/       watches a run and renders what its agents did:
                 `python -m measure.trace review develop`
    corpus.toml  the committed list of pull requests
    truth.toml   the hand-established defects of the merge request the
                 first measurement was made on — see docs/measurements.md
"""
