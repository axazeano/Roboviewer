"""The command line: `roboviewer <target> [source]` and the flags around it.

`main` is the flow — the order the steps run in and what a failure exits with.
`arguments` is the parser and how flags override the config, `console` is
everything printed, `exit_codes` is what a pipeline reads back, `ci_env` is what
a merge-request pipeline already knows about the branches, and `check_provider`
is the diagnostic behind `--check-provider`.

`roboviewer.cli:main` is the console script `pyproject.toml` installs.
"""

from __future__ import annotations

from .main import CLIError, main

__all__ = ["CLIError", "main"]
