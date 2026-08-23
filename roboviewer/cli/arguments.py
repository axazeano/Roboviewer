"""The flags, and how they override the config.

Most of what fits the tool to a model is a config setting rather than a flag,
deliberately — see docs/tuning.md. What is here is what changes per run: the
branches, the repository, where reports go, which items, which formats, and the
diagnostic modes that stop before any request is made.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..config import Config
from ..reports import renders
from . import exit_codes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roboviewer",
        description="A local, agent-driven automated reviewer for merge requests.",
        epilog=(
            "Examples:\n"
            "  roboviewer develop                     review the current branch into develop\n"
            "  roboviewer develop feature/login       review the named branch, no checkout needed\n"
            "  roboviewer release/2.0 develop         develop into a release branch\n"
            "  roboviewer -C ~/work/app develop       a repository living elsewhere\n"
            "\n"
            "Environment variables:\n"
            "  ROBOVIEWER_REPO    default repository (when -C is not given)\n"
            "  ROBOVIEWER_OUTPUT  where reports go (when --output is not given)\n"
            "  ROBOVIEWER_PROVIDER_CONFIG  the provider file, for a runner or a\n"
            "                     container with no ~/.config/roboviewer/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Target branch — what we merge into (required)",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Source branch — what we merge (defaults to the current one)",
    )
    parser.add_argument(
        "-C",
        "--repo",
        default=os.environ.get("ROBOVIEWER_REPO", "."),
        metavar="PATH",
        help=(
            "Path to the repository under review "
            "(defaults to $ROBOVIEWER_REPO or the current directory)"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=os.environ.get("ROBOVIEWER_OUTPUT"),
        metavar="PATH",
        help=(
            "Where reports go. Defaults to .roboviewer/runs inside the repository under "
            "review; point it outside to keep the working tree clean"
        ),
    )
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
    parser.add_argument("--checklist", help="Directory holding the checklist items")
    parser.add_argument("--only", help="Run only these items, comma-separated")
    parser.add_argument(
        "--format",
        type=report_formats,
        metavar="LIST",
        help=(
            "Report formats, comma-separated: md, html, sarif, codequality. "
            "Replaces report_formats from the config entirely; without the flag, as set there"
        ),
    )
    parser.add_argument(
        "--language",
        metavar="LANG",
        help=(
            "Language for the model's own text: finding titles, rationales, "
            "suggestions, the judge's summary. Takes an ISO code or a name — "
            "ru, Russian, German. Without the flag, whatever the config says"
        ),
    )
    parser.add_argument(
        "--fail-on",
        choices=exit_codes.THRESHOLDS,
        metavar="SEVERITY",
        help=(
            "Exit 1 when a confirmed finding of this severity or worse is left "
            f"standing, so a CI job goes red on it: {', '.join(exit_codes.THRESHOLDS)}. "
            "Without the flag, whatever the config says"
        ),
    )
    parser.add_argument(
        "-j", "--concurrency", type=int, help="How many items to review in parallel"
    )
    parser.add_argument("--no-judge", action="store_true", help="Skip the final judge pass")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream agent activity: tool calls, retries, errors",
    )
    parser.add_argument(
        "--list-items", action="store_true", help="Print the checklist items and exit"
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print the settings this run would use, and the file they came from",
    )
    parser.add_argument(
        "--check-provider",
        action="store_true",
        help=(
            "Make one probe request to the provider and break down the answer "
            "(for debugging 401 and friends)"
        ),
    )
    parser.add_argument("--diff-only", action="store_true", help="Print the diff summary and exit")
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
    produced the run."""
    if args.output:
        cfg.run.output_dir = str(Path(args.output).expanduser())
    if args.checklist:
        cfg.run.checklist_dir = args.checklist
    if args.format:
        # Replaces the configured list rather than extending it: --format md is a
        # way to say "just markdown this time", and appending would make that
        # impossible.
        cfg.run.report_formats = args.format
    if args.language:
        cfg.run.output_language = args.language
    if args.concurrency:
        cfg.run.concurrency = args.concurrency
    if args.fail_on:
        cfg.run.fail_on = args.fail_on
    if args.no_judge:
        cfg.run.enable_judge = False
    return cfg
