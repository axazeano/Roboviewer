"""The command: `python -m research review <target>` and `python -m research page <dir>`.

Two things to ask for. `review` runs the tool's own command with this package
watching, so the flags are the tool's flags and there is no second command line
to keep in step. `page` renders a log somebody already has — the run that was
killed, the run from last week, the run a colleague sent over.

Exit codes follow the tool's, since `review` returns whatever the review
returned: 0 nothing wrong, 1 the work did not fully happen, 2 the command could
not start.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from roboviewer.cli import main as review_main

from .recorder import Recorder
from .render import render_into

OK = 0
SETUP = 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "page":
        return _page(args.run_dir)
    return _review(args.review_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research",
        description="Watch a review and show what it did with its context.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    review = commands.add_parser(
        "review",
        help="run a review, keep a log of it, and render the page",
        # Everything after the subcommand belongs to the tool: this command adds
        # no flags of its own, so there is nothing here to shadow one of its.
        usage="python -m research review <target> [source] [roboviewer options]",
    )
    review.add_argument("review_args", nargs=argparse.REMAINDER)

    page = commands.add_parser("page", help="render the page from a log already on disk")
    page.add_argument("run_dir", type=Path, help="a run directory holding trace.jsonl")
    return parser


def _review(argv: list[str]) -> int:
    """The tool's own command, with this package listening.

    The page is rendered afterwards rather than from inside the recorder: a
    failure to render should cost the page, never the log it came from.
    """
    recorder = Recorder()
    code = review_main(argv, observer=recorder)
    # Closed again here, and a no-op when the run closed it itself: a review
    # that raised on its way out still leaves a file that is not half-written.
    recorder.closed()

    if recorder.directory is None:
        # The review stopped before it started — a bad flag, no changes, or a
        # diagnostic command. Nothing was recorded and there is nothing to show.
        return code
    _page(recorder.directory)
    return code


def _page(directory: Path) -> int:
    page = render_into(directory)
    if page is None:
        print(f"No log in {directory}: nothing was recorded there.")
        return SETUP
    print(f"Trace: {page}")
    return OK
