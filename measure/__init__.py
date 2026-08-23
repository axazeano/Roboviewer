"""The instrument for watching the tool, beside the tool.

Roboviewer reviews two branches and keeps nothing of how it got there. Seeing
what a run did rather than what it concluded needs something the tool
deliberately does not carry, and it lives here, outside the wheel —
`packages.find` in pyproject.toml includes `roboviewer*` only — so nothing on
the review path can grow a dependency on an instrument by accident.

    trace/       watches a run and renders what its agents did:
                 `python -m measure.trace review develop`

What the tool is measured on is the benchmark, `roboviewer.benchmark`, inside
the wheel behind the `benchmark` command; its data is under `benchmarks/`.
"""
