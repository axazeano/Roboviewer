"""The commands, their flags, and how a flag overrides the config.

Five commands rather than one carrying diagnostic flags: `review` and `diff`
compare two branches, `list-items`, `show-config` and `check-provider` answer a
question about the setup and never look at a branch. A flag that quietly runs
something other than a review is a command in disguise.

The branches are named — `--from`, `--into` — rather than ordered: an order
nobody can read back off a shell history is an order somebody gets wrong.

Most of what fits the tool to a model is a config setting rather than a flag,
deliberately — see docs/tuning.md. What is here is what changes per run: the
branches, the repository, where reports go, which items, which formats.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import NoReturn

from ..config import Config
from ..reports import renders
from . import exit_codes

# The commands that compare two branches; the rest need no repository at all.
COMPARING = ("review", "diff")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="roboviewer",
        description="A local, agent-driven automated reviewer for merge requests.",
        epilog=(
            "Examples:\n"
            "  roboviewer review --into develop\n"
            "        the current branch into develop\n"
            "  roboviewer review --from feature/login --into develop\n"
            "        someone else's branch, no checkout needed\n"
            "  roboviewer review --into develop --repo ~/work/app\n"
            "        a repository living elsewhere\n"
            "  roboviewer diff --into develop\n"
            "        what would be reviewed, before a token is spent\n"
            "\n"
            "Environment variables:\n"
            "  ROBOVIEWER_REPO    default repository (when --repo is not given)\n"
            "  ROBOVIEWER_OUTPUT  where reports go (when --output is not given)\n"
            "  ROBOVIEWER_PROVIDER_CONFIG  the provider file, for a runner or a\n"
            "                     container with no ~/.config/roboviewer/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)

    review = commands.add_parser(
        "review",
        help="review one branch against another",
        description=(
            "Compare the two branches, run the checklist over what changed, and write "
            "the reports. Without --from the current branch is reviewed; without --into "
            "the target a merge-request pipeline names in its environment is used."
        ),
    )
    _branches(review)
    _repository(review)
    _settings_file(review)
    _checklist_flags(review)
    review.add_argument(
        "-o",
        "--output",
        default=os.environ.get("ROBOVIEWER_OUTPUT"),
        metavar="PATH",
        help=(
            "Where reports go. Defaults to .roboviewer/runs inside the repository under "
            "review; point it outside to keep the working tree clean"
        ),
    )
    review.add_argument(
        "--format",
        type=report_formats,
        metavar="LIST",
        help=(
            "Report formats, comma-separated: md, html, sarif, codequality. "
            "Replaces report_formats from the config entirely; without the flag, as set there"
        ),
    )
    review.add_argument(
        "--language",
        metavar="LANG",
        help=(
            "Language for the model's own text: finding titles, rationales, "
            "suggestions, the judge's summary. Takes an ISO code or a name — "
            "ru, Russian, German. Without the flag, whatever the config says"
        ),
    )
    review.add_argument(
        "--fail-on",
        choices=exit_codes.THRESHOLDS,
        metavar="SEVERITY",
        help=(
            "Exit 1 when a confirmed finding of this severity or worse is left "
            f"standing, so a CI job goes red on it: {', '.join(exit_codes.THRESHOLDS)}. "
            "Without the flag, whatever the config says"
        ),
    )
    review.add_argument(
        "-j", "--concurrency", type=int, help="How many items to review in parallel"
    )
    review.add_argument("--no-judge", action="store_true", help="Skip the final judge pass")
    review.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream agent activity: tool calls, retries, errors",
    )

    diff = commands.add_parser(
        "diff",
        help="print what would be reviewed and stop",
        description=(
            "The changed files and how much of each one the review would see, without "
            "a single request to the provider."
        ),
    )
    _branches(diff)
    _repository(diff)
    _settings_file(diff)

    items = commands.add_parser(
        "list-items",
        help="print the checklist items and stop",
        description="The items this run would review with, in the order they would run.",
    )
    _repository(items)
    _settings_file(items)
    _checklist_flags(items)

    settings = commands.add_parser(
        "show-config",
        help="print the settings a run would use",
        description=(
            "Every setting a run would use and the file it came from, including which "
            "prompts, checklist and templates the repository overrides."
        ),
    )
    _repository(settings)
    _settings_file(settings)

    provider = commands.add_parser(
        "check-provider",
        help="probe the gateway and break down the answer",
        description=(
            "One plain request and one tool call to the configured gateway, with the "
            "answer read out in full — for debugging 401 and friends."
        ),
    )
    _settings_file(provider)
    return parser


def report_formats(value: str) -> list[str]:
    """`md,html` → a list of formats. Whether a format is known is checked
    later, once the config is parsed and the templates directory is known."""
    formats = [fmt.strip() for fmt in value.split(",") if fmt.strip()]
    if not formats:
        raise argparse.ArgumentTypeError(
            f"list formats separated by commas, e.g. {','.join(renders.known())}"
        )
    return formats


def apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    """A flag beats the file, because a flag is visible in the command that
    produced the run. Only `review` carries every flag below, so each is read as
    "if this command has one"."""
    flags = vars(args)
    if flags.get("output"):
        cfg.run.output_dir = str(Path(flags["output"]).expanduser())
    if flags.get("checklist"):
        cfg.run.checklist_dir = flags["checklist"]
    if flags.get("format"):
        # Replaces the configured list rather than extending it: --format md is a
        # way to say "just markdown this time", and appending would make that
        # impossible.
        cfg.run.report_formats = flags["format"]
    if flags.get("language"):
        cfg.run.output_language = flags["language"]
    if flags.get("concurrency"):
        cfg.run.concurrency = flags["concurrency"]
    if flags.get("fail_on"):
        cfg.run.fail_on = flags["fail_on"]
    if flags.get("no_judge"):
        cfg.run.enable_judge = False
    return cfg


class _Parser(argparse.ArgumentParser):
    """The one wrong call worth expecting is the old one, `roboviewer <target>
    [source]`, and "invalid choice" alone does not say what replaced it."""

    def error(self, message: str) -> NoReturn:
        if "invalid choice" in message:
            message += (
                "\nBranches are named now, not ordered: "
                "roboviewer review --from <branch> --into <branch>"
            )
        super().error(message)


def _branches(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from",
        dest="source",
        metavar="BRANCH",
        help="Source branch — what is merged (defaults to the current one)",
    )
    parser.add_argument(
        "--into",
        dest="target",
        metavar="BRANCH",
        help=(
            "Target branch — what it is merged into (defaults to the branch a "
            "merge-request pipeline names in its environment)"
        ),
    )


def _repository(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        default=os.environ.get("ROBOVIEWER_REPO", "."),
        metavar="PATH",
        help=(
            "Path to the repository under review "
            "(defaults to $ROBOVIEWER_REPO or the current directory)"
        ),
    )


def _settings_file(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "Read this file instead of ~/.config/roboviewer/config.toml. It "
            "replaces that file rather than adding to it, and carries "
            "[reviewer], [judge] and [run] — the provider is read from "
            "~/.config/roboviewer/provider.toml either way, so a file passed "
            "here can be shared without carrying a key"
        ),
    )


def _checklist_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--checklist", help="Directory holding the checklist items")
    parser.add_argument("--only", help="Run only these items, comma-separated")
