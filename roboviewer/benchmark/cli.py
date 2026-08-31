"""The command: `benchmark list|run|fetch|search`, shaped like git.

    benchmark list show                 the index, and what of it is on disk
    benchmark list add <pull-request>   record it and clone it
    benchmark list remove <id|url>      take it out of the index
    benchmark run [roboviewer flags]    review every entry with the tool
    benchmark fetch                     clone what is listed and not yet there
    benchmark search <query>            find candidates on GitHub

The order of the steps and what a failure exits with, the same split
`roboviewer.cli` uses. What happens to an entry is `fetch`, `run` and `items`;
everything printed is at the bottom of this file.

Exit codes follow the tool's: 0 nothing wrong, 1 the work did not fully happen,
2 the command could not start — an index that does not parse, an id that is not
in it, a pull request GitHub will not describe.
"""

from __future__ import annotations

import argparse
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from ..cli import exit_codes as roboviewer_exit
from ..cli import main as review_main
from . import items
from . import run as running
from .candidates import entry_toml, on_github
from .candidates.candidate import Candidate
from .candidates.criteria import SAFE_LICENCES, Filters
from .fetch import Result, fetch
from .github import GitHub, GitHubError, RateLimited, resolve_token
from .items import Entry
from .store import UNKNOWN, Store, default_root

OK = 0
INCOMPLETE = 1
SETUP = 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    # `run` hands everything it does not know to the tool; every other command
    # keeps argparse's own refusal of a stray flag.
    args, review_args = parser.parse_known_args(argv)
    if review_args and args.command != "run":
        parser.error(f"unrecognized arguments: {' '.join(review_args)}")
    store = Store(Path(args.root).expanduser() if args.root else default_root())
    try:
        if args.command == "list":
            return _list(args, store)
        if args.command == "run":
            return _run(args, store, review_args)
        if args.command == "fetch":
            return _fetch(args, store)
        return _search(args)
    except (FileNotFoundError, ValueError) as exc:
        # The index could not be read, or names nothing of the sort asked for
        print(f"Index error: {exc}", file=sys.stderr)
        return SETUP


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        # A tool flag must never be read as a prefix of one of ours
        allow_abbrev=False,
        description=(
            "Review a fixed list of merge requests with roboviewer: keep the list, "
            "clone what it names at the commits reviewers saw, and run the tool over it."
        ),
        epilog=(
            "Examples:\n"
            "  benchmark list add https://github.com/cli/cli/pull/13946\n"
            "  benchmark list show\n"
            "  benchmark run --no-judge --format md\n"
            "  benchmark run --entries cli-13946 -v\n"
            '  benchmark search "is:pr is:merged language:Go review:changes_requested" '
            "--min-files 30\n"
            "\n"
            "Environment variables:\n"
            "  ROBOVIEWER_BENCHMARKS  the benchmarks directory (when --root is not given)\n"
            "  GITHUB_TOKEN           raises the rate limit, lets `search` run, and is the\n"
            "                         only way to read whether a review thread was resolved\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        help=(
            "The benchmarks directory: items.toml, references/, repos/, comments/, runs/. "
            "Defaults to $ROBOVIEWER_BENCHMARKS, then to ./benchmarks"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="the index of merge requests")
    actions = listing.add_subparsers(dest="action", required=True)
    actions.add_parser("show", help="print the index, and whether each entry is on disk")
    add = actions.add_parser(
        "add",
        help="record a pull request in the index and clone it",
        description=(
            "Ask GitHub about the pull request, write an [[entry]] for it — base, the "
            "commit reviewers saw, size, language, licence — and clone the repository. "
            "`domain` and `found` are left for you: see docs/benchmark-selection.md."
        ),
    )
    add.add_argument("url", help="https://github.com/<owner>/<repo>/pull/<number>")
    add.add_argument(
        "--no-fetch", action="store_true", help="Write the entry and stop; `fetch` clones later"
    )
    remove = actions.add_parser("remove", help="take an entry out of the index")
    remove.add_argument("key", metavar="ID|URL", help="the entry's id, or its pull request URL")

    run = commands.add_parser(
        "run",
        help="review every entry with roboviewer, flags passed through",
        usage=(
            "benchmark run [--entries IDS] [--repeats N] [--parallel N] [--refresh] "
            "[roboviewer options]"
        ),
        allow_abbrev=False,
        description=(
            "One roboviewer run per entry, in its clone, between its two commits, with "
            "whatever flags follow — --no-judge, --format, -v, --config and the rest. "
            "Reports go under <root>/runs/<stamp>/<id>/ unless -o says otherwise; a "
            "summary of the whole run is written beside them."
        ),
    )
    _entries_flags(run)
    run.add_argument(
        "--repeats",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Review every entry this many times (default 1). The summary then reports "
            "statistics over the repeats: tokens, time, self-consistency"
        ),
    )
    run.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Review this many entries at once (default 1). Repeats of one entry stay "
            "sequential; each review still paces itself, so keep this modest"
        ),
    )

    fetch_cmd = commands.add_parser(
        "fetch",
        help="clone what the index names and is not on disk yet",
        description=(
            "One clone per entry, positioned at the commit reviewers saw, with their "
            "comments saved beside it. An entry already there is left alone."
        ),
    )
    _entries_flags(fetch_cmd)

    search = commands.add_parser(
        "search",
        help="find candidates on GitHub",
        description=(
            "Find candidates for the benchmark. GitHub search has no qualifier for how "
            "much a pull request changed, so the size comes back with the results and "
            "is filtered here."
        ),
        epilog=(
            "Examples:\n"
            '  benchmark search "is:pr is:merged language:Go" --min-files 30\n'
            '  benchmark search "is:pr is:merged review:changes_requested" '
            "--min-files 40 --min-stars 500 --toml\n"
            "\n"
            "It does not decide whether a review found a defect — that is what\n"
            "docs/benchmark-selection.md is for, and it is a person's call.\n"
            "\n"
            "Environment variables:\n"
            "  GITHUB_TOKEN  required: the search API is GraphQL and GraphQL needs one\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _search_flags(search)
    return parser


def _entries_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--entries", metavar="IDS", help="Only these entries, comma-separated (default: all)"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch again even when the entry is already on disk (the clone is reused)",
    )


def _search_flags(parser: argparse.ArgumentParser) -> None:
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
        help="Print [[entry]] blocks to paste into the index (implies --heads)",
    )


# ------------------------------------------------------------------ list


def _list(args: argparse.Namespace, store: Store) -> int:
    if args.action == "show":
        return _show(store)
    if args.action == "add":
        return _add(args, store)
    entry = items.remove(store.items, args.key)
    print(f"✔ {entry.id} removed from {store.items}")
    if store.repo_dir(entry).exists():
        print(f"  The clone is still at {store.repo_dir(entry)}; delete it when you are done.")
    return OK


def _show(store: Store) -> int:
    entries = items.load_items(store.items, allow_empty=True)
    if not entries:
        print(f"Nothing in {store.items}: `benchmark list add <pull-request>` starts it.")
        return OK
    print(f"▸ {len(entries)} entr(ies) in {store.items}")
    for entry in entries:
        mark = "•" if store.is_built(entry) else "○"
        print(
            f"{mark} {entry.id:<24} {entry.base[:12]} → {entry.head[:12]}  "
            f"{entry.language or '?':<10} {entry.files:>4} files  "
            f"{entry.added:>6}+/{entry.removed:<6}-  {entry.url}"
        )
    print()
    print("• on disk  ○ not fetched yet")
    return OK


def _add(args: argparse.Namespace, store: Store) -> int:
    pull = items.parse_pull_url(args.url)
    github = GitHub(token=resolve_token())
    try:
        described = on_github.describe(github, pull)
    except (GitHubError, RateLimited) as exc:
        print(f"✗ {pull.slug}#{pull.number}: {exc}", file=sys.stderr)
        return SETUP
    entry = described.entry
    items.append(store.items, entry)
    print(f"✔ {entry.id:<24} added to {store.items}")
    print(f"  base {entry.base[:12]} → head {entry.head[:12]}, {len(described.threads)} thread(s)")
    if not described.reviewed:
        print(
            "  No review thread names this head, so it is the branch tip — a state "
            "the reviewers may never have looked at. Check the pull request.",
            file=sys.stderr,
        )
    print("  `domain` and `found` are blank; fill them in before committing the index.")
    if args.no_fetch:
        return OK
    _say_cloning(entry)
    try:
        result = fetch(entry, store, github)
    except RateLimited as exc:
        print(f"✗ {entry.id}: {exc}", file=sys.stderr)
        return INCOMPLETE
    _entry_line(result, github)
    if result.ok:
        print(
            f"Review it: roboviewer review --from {entry.head[:12]} "
            f"--into {entry.base[:12]} --repo {store.repo_dir(entry)}"
        )
    return OK if result.ok else INCOMPLETE


# ------------------------------------------------------------------ run


def _run(args: argparse.Namespace, store: Store, review_args: list[str]) -> int:
    if args.parallel < 1:
        raise ValueError(f"--parallel {args.parallel}: at least one entry reviews at a time")
    entries = items.select(items.load_items(store.items), args.entries)
    flags = _review_flags(review_args)
    benchmark = running.start(store, flags, repeats=args.repeats)
    github = GitHub(token=resolve_token())
    times = f" x {benchmark.repeats}" if benchmark.repeats > 1 else ""
    print(f"▸ {len(entries)} entr(ies){times} → {benchmark.directory}")
    if flags:
        print(f"  roboviewer {' '.join(flags)}")

    if args.parallel > 1:
        _review_in_parallel(benchmark, entries, store, github, args)
    else:
        for entry in entries:
            if not _review_repeats(benchmark, entry, store, github, args):
                break

    summary = running.write_summary(benchmark)
    _run_summary(benchmark, summary)
    expected = len(entries) * benchmark.repeats
    return OK if benchmark.ok and len(benchmark.outcomes) == expected else INCOMPLETE


def _review_in_parallel(
    benchmark: running.Benchmark,
    entries: list[Entry],
    store: Store,
    github: GitHub,
    args: argparse.Namespace,
) -> None:
    """At most `--parallel` entries at once, each worker walking one entry's
    repeats in order — so run directories and the `latest` link inside an
    entry's folder are never raced. A stop — a rate limit, a tool that cannot
    start — lets running reviews finish and starts no new entry. Every line a
    worker prints is prefixed with its entry id, or the streams would be
    unreadable."""
    stop = threading.Event()

    def review(entry: Entry) -> None:
        if stop.is_set():
            return
        _prefix_current_thread(f"{entry.id:<24}| ")
        if not _review_repeats(benchmark, entry, store, github, args, stop=stop):
            stop.set()

    with _prefixed_output(), ThreadPoolExecutor(max_workers=args.parallel) as pool:
        for _ in pool.map(review, entries):
            pass


def _review_repeats(
    benchmark: running.Benchmark,
    entry: Entry,
    store: Store,
    github: GitHub,
    args: argparse.Namespace,
    stop: threading.Event | None = None,
) -> bool:
    """Every attempt of one entry. False when the whole run must stop — a rate
    limit, or a tool that could not start and would refuse every entry alike.
    `stop` is that signal arriving from another worker: no further attempt
    starts once it is set."""
    for attempt in range(1, benchmark.repeats + 1):
        if stop is not None and stop.is_set():
            return False
        counter = f"  {attempt}/{benchmark.repeats}" if benchmark.repeats > 1 else ""
        print(f"── {entry.id}{counter}  {entry.url}")
        refresh = args.refresh and attempt == 1
        if refresh or not store.is_built(entry):
            _say_cloning(entry)
        try:
            outcome = running.review_entry(
                benchmark,
                entry,
                store,
                github,
                # The clone is fetched once; the repeats review the same one
                refresh=refresh,
                review=review_main,
            )
        except RateLimited as exc:
            # Every entry still to fetch would be refused the same way
            print(f"✗ {entry.id}: {exc}", file=sys.stderr)
            return False
        _outcome_line(outcome)
        if outcome.code == roboviewer_exit.SETUP:
            # Exit 2 is "the tool could not start" — a missing key, a broken
            # config. That is about the machine, not the entry, and the next
            # entry would only fail the same way after another clone.
            print(
                "Stopping: roboviewer could not start, and the remaining entries "
                "would fail the same way. If it really is about this entry, rerun "
                f"it alone: benchmark run --entries {entry.id}",
                file=sys.stderr,
            )
            return False
        if outcome.status == "not_fetched" or outcome.crashed:
            # The other attempts would be refused the same clone, or crash on
            # the same review, the same way
            return True
    return True


def _review_flags(remainder: list[str]) -> list[str]:
    """argparse leaves a leading `--` in the remainder when someone separates
    the tool's flags from ours with it; the tool would refuse it."""
    return remainder[1:] if remainder[:1] == ["--"] else list(remainder)


# ------------------------------------------------------------------ fetch


def _fetch(args: argparse.Namespace, store: Store) -> int:
    entries = items.select(items.load_items(store.items), args.entries)
    github = GitHub(token=resolve_token())
    _fetch_header(entries, store, github)

    results: list[Result] = []
    for entry in entries:
        try:
            result = fetch(entry, store, github, refresh=args.refresh)
        except RateLimited as exc:
            # Every entry after this one would be refused the same way
            print(f"✗ {entry.id}: {exc}", file=sys.stderr)
            _fetch_summary(results, store)
            return INCOMPLETE
        results.append(result)
        _entry_line(result, github)

    _fetch_summary(results, store)
    return OK if all(result.ok for result in results) else INCOMPLETE


# ------------------------------------------------------------------ search


def _search(args: argparse.Namespace) -> int:
    """The sieve, not the judge.

    Prints what passed, and with --toml the entries to paste. Whether a review
    found a defect or a naming preference is a person's call, and the threads
    are printed so it can be made without opening the pull request.
    """
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
        _with_head(github, candidate, with_toml=args.toml)
    return OK


# ------------------------------------------------------------------ printing


def _fetch_header(entries: list[Entry], store: Store, github: GitHub) -> None:
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


def _fetch_summary(results: list[Result], store: Store) -> None:
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
            f"Review one: roboviewer review --from {example.entry.head[:12]} "
            f"--into {example.entry.base[:12]} --repo {store.repo_dir(example.entry)}"
        )


@contextmanager
def _prefixed_output() -> Iterator[None]:
    """stdout and stderr wrapped so each worker thread's lines carry its entry
    prefix; a thread that set none — the main one — writes through unchanged."""
    out, err = _PrefixedStream(sys.stdout), _PrefixedStream(sys.stderr)
    sys.stdout, sys.stderr = out, err
    try:
        yield
    finally:
        sys.stdout, sys.stderr = out.stream, err.stream


def _prefix_current_thread(prefix: str) -> None:
    """Only effective under `_prefixed_output`; harmless anywhere else."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, _PrefixedStream):
            stream.set_prefix(prefix)


class _PrefixedStream:
    """Per-thread line prefixes over one real stream.

    Lines are buffered per thread until their newline and written whole under
    a lock: `print` issues the text and the newline as two writes, and without
    the buffering two workers' halves would splice mid-line.
    """

    def __init__(self, stream: IO[str]) -> None:
        self.stream = stream
        self._local = threading.local()
        self._write_lock = threading.Lock()

    def set_prefix(self, prefix: str) -> None:
        self._local.prefix = prefix

    def write(self, text: str) -> int:
        prefix = getattr(self._local, "prefix", "")
        if not prefix:
            with self._write_lock:
                return self.stream.write(text)
        pending = getattr(self._local, "pending", "") + text
        complete, newline, rest = pending.rpartition("\n")
        if newline:
            whole = complete + newline
            with self._write_lock:
                self.stream.write("".join(prefix + line for line in whole.splitlines(True)))
        self._local.pending = rest
        return len(text)

    def flush(self) -> None:
        pending = getattr(self._local, "pending", "")
        if pending:
            with self._write_lock:
                self.stream.write(getattr(self._local, "prefix", "") + pending)
            self._local.pending = ""
        self.stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.stream, name)


def _say_cloning(entry: Entry) -> None:
    """Said before the silence: a full-history clone of a real repository can
    run for minutes, and nothing else is printed until it is done."""
    print(f"  cloning {entry.pull.clone_url} — full history, this can take a while")


def _outcome_line(outcome: running.Outcome) -> None:
    if outcome.status == "not_fetched":
        print(f"✗ {outcome.entry.id:<24} not fetched: {outcome.detail}", file=sys.stderr)
        return
    if outcome.status == "stopped":
        print(f"✗ {outcome.entry.id:<24} {outcome.detail}", file=sys.stderr)
        return
    print(
        f"✔ {outcome.entry.id:<24} {outcome.findings} finding(s), "
        f"{outcome.confirmed} confirmed, {outcome.out_of_scope} out of scope, "
        f"{outcome.seconds:.0f}s → {outcome.directory or '?'}"
    )


def _run_summary(benchmark: running.Benchmark, summary: Path) -> None:
    reviewed = [o for o in benchmark.outcomes if o.ok]
    failed = [o for o in benchmark.outcomes if not o.ok]
    print()
    print(
        f"{len(reviewed)} reviewed, {len(failed)} not: "
        f"{sum(o.findings for o in reviewed)} finding(s), "
        f"{sum(o.confirmed for o in reviewed)} confirmed"
    )
    if failed:
        print(f"Not reviewed: {', '.join(o.entry.id for o in failed)}", file=sys.stderr)
    print(f"Summary: {summary}")


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


def _with_head(github: GitHub, candidate: Candidate, *, with_toml: bool) -> None:
    """The head, and the review at it. Printing what reviewers said is the point:
    the criteria turn on whether they found a defect, and that is unreadable from
    a count of threads."""
    try:
        head, threads = on_github.propose_head(github, items.parse_pull_url(candidate.url))
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
            "(docs/benchmark-selection.md).",
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
    if with_toml:
        print()
        print(entry_toml.from_candidate(candidate, head))
