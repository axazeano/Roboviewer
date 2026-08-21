"""The commands: `python -m corpus <list.toml>` and `python -m corpus find`.

The order of the steps and what a failure exits with, the same split
`roboviewer.cli` uses. Everything that decides what happens to an entry is in
`build`; everything printed is at the bottom of this file.

`find` is a second door onto the same package rather than a subcommand tree:
the build command takes a path as its first argument and has since the first
version, so `find` is recognised by that word alone and everything else reaches
the builder unchanged.

Exit codes follow the tool's: 0 nothing wrong, 1 the work did not fully happen,
2 the command could not start — a list that does not parse, an id that is not
in it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import Result, build
from .candidates import on_github
from .candidates.criteria import SAFE_LICENCES, Candidate, Filters
from .candidates.entry_text import as_toml
from .entries import Entry, load_list, select
from .github import GitHub, GitHubError, RateLimited, resolve_token
from .store import UNKNOWN, Store, default_root

OK = 0
INCOMPLETE = 1
SETUP = 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "find":
        return find_main(argv[1:])
    args = build_parser().parse_args(argv)
    try:
        entries = select(load_list(Path(args.list)), args.only)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Corpus list error: {exc}", file=sys.stderr)
        return SETUP

    store = Store(Path(args.corpus).expanduser() if args.corpus else default_root())
    github = GitHub(token=resolve_token())
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



def find_main(argv: list[str]) -> int:
    """`python -m corpus find` — the sieve, not the judge.

    Prints what passed, and with --toml the entries to paste. Whether a review
    found a defect or a naming preference is a person's call, and the threads
    are printed so it can be made without opening the pull request.
    """
    args = find_parser().parse_args(argv)
    github = GitHub(token=resolve_token())
    if github.token is None:
        print(
            "  No token: search is GraphQL and GraphQL needs one. Run `gh auth login`, "
            "or set GITHUB_TOKEN.",
            file=sys.stderr,
        )
        return SETUP
    try:
        result = on_github.search(
            github,
            args.query,
            Filters(
                min_files=args.min_files,
                min_threads=args.min_threads,
                min_stars=args.min_stars,
                licences=None if args.any_license else SAFE_LICENCES,
            ),
            pages=args.pages,
        )
    except (GitHubError, RateLimited) as exc:
        print(f"Search failed: {exc}", file=sys.stderr)
        return INCOMPLETE

    candidates = sorted(result.candidates, key=lambda c: c.files, reverse=True)[: args.limit]
    _search_header(result)
    if not candidates:
        return OK

    for candidate in candidates:
        _candidate_line(candidate)
    if not (args.toml or args.heads):
        print()
        print("Add --heads for the commit reviewers saw, --toml for entries to paste.")
        return OK

    print()
    for candidate in candidates:
        _with_head(github, candidate, as_toml_too=args.toml)
    return OK


def find_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m corpus find",
        description=(
            "Find candidates for the corpus. GitHub search has no qualifier for "
            "how much a pull request changed, so the size comes back with the "
            "results and is filtered here."
        ),
        epilog=(
            "Examples:\n"
            '  python -m corpus find "is:pr is:merged language:Go" --min-files 30\n'
            '  python -m corpus find "is:pr is:merged review:changes_requested" '
            "--min-files 40 --min-stars 500 --toml\n"
            "\n"
            "It does not decide whether a review found a defect — that is what\n"
            "docs/corpus-selection.md is for, and it is a person's call.\n"
            "\n"
            "Environment variables:\n"
            "  GITHUB_TOKEN  required: the search API is GraphQL and GraphQL needs one\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", help="A GitHub pull-request search query")
    parser.add_argument(
        "--min-files",
        type=int,
        default=0,
        metavar="N",
        help="Keep only pull requests touching at least this many files",
    )
    parser.add_argument(
        "--min-threads",
        type=int,
        default=1,
        metavar="N",
        help="Keep only pull requests with at least this many review threads (default 1)",
    )
    parser.add_argument(
        "--min-stars", type=int, default=0, metavar="N", help="Keep only repositories this popular"
    )
    parser.add_argument(
        "--pages", type=int, default=5, metavar="N", help="How many pages of 50 to read (default 5)"
    )
    parser.add_argument(
        "--limit", type=int, default=20, metavar="N", help="Show at most this many (default 20)"
    )
    parser.add_argument(
        "--any-license",
        action="store_true",
        help=(
            "Keep every licence, including ones GitHub could not name. Dropped by "
            "default: an entry records a licence, and only a licence somebody "
            "listed as safe can be recorded without reading the repository"
        ),
    )
    parser.add_argument(
        "--heads",
        action="store_true",
        help="For each candidate, the commit reviewers saw and what they said there",
    )
    parser.add_argument(
        "--toml",
        action="store_true",
        help="Print [[entry]] blocks to paste into the corpus list (implies --heads)",
    )
    return parser


def _header(entries: list[Entry], store: Store, github: GitHub) -> None:
    print(f"▸ {len(entries)} entr(ies) → {store.root}")
    if not github.resolution_known:
        print(
            "  No token: 60 requests an hour, and thread resolution cannot be "
            "read. Run `gh auth login`, or set GITHUB_TOKEN, for both."
        )


def _entry_line(result: Result, github: GitHub) -> None:
    mark = {"cached": "•", "built": "✔", "failed": "✗"}[result.status]
    stream = sys.stderr if result.status == "failed" else sys.stdout
    print(f"{mark} {result.entry.id:<24} {result.detail}", file=stream)
    if result.reviewed_head:
        print(
            f"  The review was written against {result.reviewed_head[:12]}, not the head "
            f"{result.entry.head[:12]} this entry names. Whatever reviewers asked for is "
            "already fixed at this head, so the entry measures nothing — unless a later "
            "round is what you meant.",
            file=sys.stderr,
        )
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
    misplaced = [r for r in results if r.reviewed_head]
    if misplaced:
        print(
            "Head is past the review: "
            + ", ".join(f"{r.entry.id} → {r.reviewed_head[:12]}" for r in misplaced),
            file=sys.stderr,
        )
    example = next((r for r in results if r.ok), None)
    if example is not None:
        print(
            f"Review one: roboviewer {example.entry.base[:12]} {example.entry.head[:12]} "
            f"-C {store.repo_dir(example.entry)}"
        )


def _search_header(result: on_github.Search) -> None:
    """What was read, and — when it matters — why it stopped. The ceiling and an
    exhausted page budget look the same in the output and have opposite fixes."""
    print(
        f"▸ {result.matched} match the query, {result.scanned} read, "
        f"{len(result.candidates)} pass the filters"
    )
    if result.truncated:
        print(
            "  GitHub search stops at 1000 results however far the cursor is walked. "
            "Narrow the query — a shorter created: window — rather than adding pages.",
            file=sys.stderr,
        )
    elif result.stopped_early:
        print(f"  Read {result.scanned} of them; --pages reads further.")
    for reason, count in sorted(result.rejected.items(), key=lambda pair: -pair[1]):
        print(f"  {count:>4} dropped — {reason.why}")
    _why_nothing_passed(result)

def _why_nothing_passed(result: on_github.Search) -> None:
    """A run that found nothing usually died on one filter, and the fix depends
    on which. The fix is the reason's own — see `candidates.criteria` — so the two
    cannot drift apart."""
    if result.candidates or not result.worst:
        return
    reason, _ = result.worst
    print(f"  {reason.fix}")


def _candidate_line(candidate: Candidate) -> None:
    print(
        f"  {candidate.files:>4} files  {candidate.added:>6}+/{candidate.removed:<6}- "
        f"{candidate.threads:>3} threads  {candidate.stars:>7} ★  "
        f"{candidate.license or '?':<12} {candidate.url}"
    )


def _with_head(github: GitHub, candidate: Candidate, *, as_toml_too: bool) -> None:
    """The head, and the review at it. Printing what reviewers said is the point:
    the criteria turn on whether they found a defect, and that is unreadable from
    a count of threads."""
    try:
        head, threads = on_github.propose_head(github, candidate)
    except (GitHubError, RateLimited) as exc:
        print(f"✗ {candidate.id}: {exc}", file=sys.stderr)
        return

    print(f"── {candidate.id}  {candidate.url}")
    if not head and threads:
        # GraphQL resolves originalCommit rather than reading a stored SHA, so a
        # null there means GitHub cannot reach the commit any more. Reading the
        # SHA off the pull request by hand does not help: nothing can clone it.
        print(
            "   Every thread was written against a commit GitHub can no longer reach — "
            "force-pushed and gone. The entry cannot be rebuilt, which disqualifies it "
            "(docs/corpus-selection.md).",
            file=sys.stderr,
        )
    elif not head:
        print(
            "   The search counted review threads and none came back; nothing here "
            "names a head. Worth a look at the pull request before trusting either "
            "number.",
            file=sys.stderr,
        )
    else:
        print(f"   base {candidate.base[:12]} → head {head[:12]} (the commit reviewers saw)")
    for thread in threads[:5]:
        first = thread.comments[0] if thread.comments else None
        if first is None:
            continue
        said = " ".join(first.body.split())[:100]
        print(f"   · {thread.file}:{thread.line or '?'} @{first.author}: {said}")
    if len(threads) > 5:
        print(f"   · [... {len(threads) - 5} more thread(s), not shown ...]")
    if as_toml_too:
        print()
        print(as_toml(candidate, head))
