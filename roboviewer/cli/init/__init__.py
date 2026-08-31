"""`roboviewer init` — the setup, asked rather than copied.

Everything else the tool does starts from two files in `~/.config/roboviewer/`,
and until now the way to get them was to find the examples in a clone, copy
them, and work out which of forty documented keys actually have to be set. The
questions here are that shortlist: the gateway, the key, the model, and the few
run settings a person has an opinion about on the first day.

What it writes is not a file of its own making — it is the annotated example
with the answers set into it, so the result is still the reference text.
`wizard` is the interview, `questions` is how one is asked, and the editing
itself is `config.example`.
"""

from __future__ import annotations

import sys

from .. import console, exit_codes
from .questions import Cancelled, Questions
from .wizard import Wizard

__all__ = ["Cancelled", "Questions", "Wizard", "run_init"]


def run_init() -> int:
    """The command. It is a conversation, so a stdin that cannot hold one is
    turned away rather than left to hang against a runner's closed stream."""
    if not sys.stdin.isatty():
        console.error(
            "init asks questions, and this stdin cannot answer them.",
            "Run it at a keyboard. A runner or a container is configured by "
            "mounting the two files instead — see docs/ci.md.",
        )
        return exit_codes.SETUP

    try:
        return Wizard(Questions()).run()
    except (Cancelled, KeyboardInterrupt):
        print()
        console.error("Stopped. Nothing was written.")
        return exit_codes.SETUP
