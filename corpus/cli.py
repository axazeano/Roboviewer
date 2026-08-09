"""The command: `python -m corpus <list.toml>`.

The order of the steps and what a failure exits with, the same split
`roboviewer.cli` uses. Everything that decides what happens to an entry is in
`build`; everything printed is at the bottom of this file.

Exit codes follow the tool's: 0 nothing wrong, 1 the work did not fully happen,
2 the command could not start — a list that does not parse, an id that is not
in it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import Result, build
from .entries import Entry, load_list, select
from .github import GitHub, RateLimited, token_from_env
from .store import UNKNOWN, Store, default_root

OK = 0
INCOMPLETE = 1
SETUP = 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        entries = select(load_list(Path(args.list)), args.only)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Corpus list error: {exc}", file=sys.stderr)
        return SETUP

    store = Store(Path(args.corpus).expanduser() if args.corpus else default_root())
    github = GitHub(token=token_from_env())
    _header(entries, store, github)

    results: list[Result] = []
    for entry in entries:
        try:
            result = build(entry, store, github, refresh=args.refresh)
        except RateLimited as exc:
            # Every entry after this one would be refused the same way
            print(f"✗ {entry.id}: {exc}", file=sys.stderr)
            _summary(results, store)
            return INCOMPLETE
        results.append(result)
        _entry_line(result, github)

    _summary(results, store)
    return OK if all(result.ok for result in results) else INCOMPLETE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m corpus",
        description=(
            "Build the corpus the review baseline is measured on: one clone per "
            "pull request, positioned at the commits reviewers saw, with their "
            "comments saved beside it."
        ),
        epilog=(
            "Examples:\n"
            "  python -m corpus corpus.toml              build or refresh everything\n"
            "  python -m corpus corpus.toml --only a,b   just these entries\n"
            "  python -m corpus corpus.toml --refresh    fetch again, ignoring what is built\n"
            "\n"
            "Environment variables:\n"
            "  ROBOVIEWER_CORPUS  where the corpus lives (when --corpus is not given)\n"
            "  GITHUB_TOKEN       raises the rate limit, and is the only way to read\n"
            "                     whether a review thread was resolved\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("list", help="The committed list of pull requests (TOML)")
    parser.add_argument(
        "--corpus",
        metavar="PATH",
        help=(
            "Where the clones go. Defaults to $ROBOVIEWER_CORPUS, then to "
            "~/.cache/roboviewer/corpus — outside any repository under measurement"
        ),
    )
    parser.add_argument("--only", metavar="IDS", help="Build only these entries, comma-separated")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch again even when the entry is already built (the clone is reused)",
    )
    return parser


def _header(entries: list[Entry], store: Store, github: GitHub) -> None:
    print(f"▸ {len(entries)} entr(ies) → {store.root}")
    if not github.resolution_known:
        print(
            "  No token: 60 requests an hour, and thread resolution cannot be "
            "read. Set GITHUB_TOKEN for both."
        )


def _entry_line(result: Result, github: GitHub) -> None:
    mark = {"cached": "•", "built": "✔", "failed": "✗"}[result.status]
    stream = sys.stderr if result.status == "failed" else sys.stdout
    print(f"{mark} {result.entry.id:<24} {result.detail}", file=stream)
    if result.status == "cached" and result.resolution == UNKNOWN and github.resolution_known:
        # Built anonymously, and the token now available would fill in the gap
        print("  built without a token, so no thread resolution — --refresh adds it")


def _summary(results: list[Result], store: Store) -> None:
    built = [r for r in results if r.status == "built"]
    cached = [r for r in results if r.status == "cached"]
    failed = [r for r in results if r.status == "failed"]
    print()
    print(f"{len(built)} built, {len(cached)} already there, {len(failed)} failed")
    if failed:
        print(f"Failed: {', '.join(r.entry.id for r in failed)}", file=sys.stderr)
    example = next((r for r in results if r.ok), None)
    if example is not None:
        print(
            f"Review one: roboviewer {example.entry.base[:12]} {example.entry.head[:12]} "
            f"-C {store.repo_dir(example.entry)}"
        )
