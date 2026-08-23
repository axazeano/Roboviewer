"""The benchmark: a fixed list of merge requests, and the tool run over them.

`roboviewer` reads two git branches and never talks to a forge; finding out how
well it reviews needs real merge requests on disk, positioned at the commits
reviewers saw, and a way to run the tool over all of them with one command.
That is this package, and the `benchmark` console script in front of it:

    benchmark list add <pull-request>    record it in the index and clone it
    benchmark list show | remove <id>
    benchmark run [roboviewer flags]     review every entry, one run per entry
    benchmark fetch                      clone what is listed and not yet there
    benchmark search <query>             candidates on GitHub, sieved by size

Everything lives under one directory, `benchmarks/` by default — `items.toml`
the index, `references/` what a good review of an entry finds, `repos/` the
clones, `comments/` what reviewers said, `runs/` what the tool produced — see
`store`. The only forge code in the wheel is here, and nothing on the review
path imports it. See docs/benchmark.md.
"""
