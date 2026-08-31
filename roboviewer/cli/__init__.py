"""The command line: `roboviewer review --from <branch> --into <branch>`, the
four commands beside it and the flags around them.

`main` is the flow — the order the steps run in and what a failure exits with.
`arguments` is the parser and how flags override the config, `console` is
everything printed, `exit_codes` is what a pipeline reads back, `ci_env` is what
a merge-request pipeline already knows about the branches, and `check_provider`
is what the `check-provider` command does.

`roboviewer.cli:main` is the console script `pyproject.toml` installs.
"""

from __future__ import annotations

from .main import CLIError, main

__all__ = ["CLIError", "main"]
