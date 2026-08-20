# Command line

Every flag a run takes, and the environment variables that stand in for the ones
you would otherwise retype.

```bash
roboviewer <target> [source]
```

The target branch is required. The source defaults to your current branch, and
naming it explicitly lets you review someone else's branch without checking it
out.

```bash
roboviewer develop                    # current branch into develop
roboviewer develop feature/login      # someone else's branch
roboviewer -C ~/projects/app develop  # a repository living elsewhere
```

| Flag | What it does |
| --- | --- |
| `-C, --repo` | Repository to review; defaults to `$ROBOVIEWER_REPO` or the current directory |
| `-o, --output` | Where reports go; point it outside the repository to keep `git status` clean |
| `--diff-only` | Show what would be reviewed and stop, without spending tokens |
| `--only correctness,tests` | Run just these checklist items |
| `-v, --verbose` | Stream agent activity: tool calls, retries, errors |
| `--format md,html` | Which reports to write; HTML is one self-contained file |
| `--language ru` | Language the model writes findings in |
| `--fail-on major` | Exit 1 on a confirmed finding this bad or worse, so CI goes red |
| `--config PATH` | Read these settings instead of `~/.config/roboviewer/config.toml`; the provider still comes from `provider.toml`, and a `[provider]` section here is refused |
| `--check-provider` | Diagnose the gateway and stop |

`ROBOVIEWER_REPO` and `ROBOVIEWER_OUTPUT` cover `-C`/`-o` if you set them once,
and `ROBOVIEWER_PROVIDER_CONFIG` names the provider file where there is no
`~/.config/roboviewer/` — a runner, a container.
`--help` has the rest.

Most of what fits a tool to a model is a config setting rather than a flag, and
[Tuning](tuning.md) explains which way round and why.
