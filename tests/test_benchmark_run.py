"""`benchmark run`: the tool over every entry, with its own flags.

The review itself is stubbed — what is under test is the harness around it:
that each entry is reviewed in its clone between its two commits, that the
flags reach the tool unchanged, that an entry which cannot be fetched does not
stop the others, and that the summary says what each review came to.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from roboviewer.benchmark import run as running
from roboviewer.benchmark.cli import main
from roboviewer.benchmark.github import GitHub
from roboviewer.benchmark.store import Store
from roboviewer.models import Finding, ItemResult, ReviewRun, Usage, Verdict
from roboviewer.observer import RunObserver

from .test_benchmark_fetch import (
    MISSING_SHA,
    REST_COMMENTS,
    Origin,
    entry_for,
    make_origin,
    pointing_at,
    rate_limited,
    transport_for,
)


class FakeReview:
    """Stands in for `roboviewer.cli.main`: remembers what it was called with
    and reports a run with two findings, one of them judged away. The first
    `crash_first` calls raise instead of exiting, the way the real tool once
    let a timeout escape."""

    def __init__(self, code: int = 0, crash_first: int = 0) -> None:
        self.code = code
        self.crash_first = crash_first
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], observer: RunObserver) -> int:
        self.calls.append(list(argv))
        if len(self.calls) <= self.crash_first:
            raise RuntimeError("the provider timed out")
        root = Path(argv[argv.index("--repo") + 1])
        output = Path(argv[argv.index("-o") + 1]) if "-o" in argv else root / ".roboviewer"
        target, source = argv[argv.index("--into") + 1], argv[argv.index("--from") + 1]
        run = ReviewRun(
            run_id="run-1",
            repo_root=str(root),
            branch=source,
            target=target,
            base_sha=target,
            head_sha=source,
            model="stub",
            started_at="2026-08-23T10:00:00Z",
            items=[
                ItemResult(
                    item_id="i1",
                    item_title="item",
                    status="ok",
                    usage=Usage(prompt_tokens=100, completion_tokens=10, cached_tokens=40),
                )
            ],
            findings=[
                Finding(id="f1", file="cart.py", line=2, title="Drops a line", rationale="."),
                Finding(id="f2", file="cart.py", line=1, title="Nothing", rationale="."),
            ],
            verdicts={"f2": Verdict(finding_id="f2", verdict="false_positive")},
            judge_usage=Usage(prompt_tokens=50, completion_tokens=5, cached_tokens=20),
        )
        observer.run_started(run, output / root.name / run.run_id)
        observer.run_finished(run, "done")
        return self.code


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return make_origin(tmp_path / "origin")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "benchmarks")


@pytest.fixture
def anonymous() -> GitHub:
    return GitHub(token=None, transport=transport_for(REST_COMMENTS))


def test_each_entry_is_reviewed_in_its_clone_between_its_two_commits(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    review = FakeReview()
    benchmark = running.start(store, ["--no-judge", "-v"], stamp="stamp")

    outcome = running.review_entry(benchmark, entry, store, anonymous, review=review)

    assert outcome.status == "reviewed"
    assert outcome.findings == 2
    assert outcome.confirmed == 1
    [argv] = review.calls
    assert argv[:7] == [
        "review", "--into", origin.base, "--from", origin.head,
        "--repo", str(store.repo_dir(entry)),
    ]
    assert argv[7:9] == ["-o", str(store.runs / "stamp")]
    assert argv[9:] == ["--no-judge", "-v"]
    assert store.is_built(entry), "fetched on the way, because it was not there"


def test_a_relative_root_still_sends_the_tool_an_absolute_output_path(
    origin: Origin, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The tool resolves a relative -o against the repository it reviews — the
    entry's clone — which would bury every report inside the cache."""
    pointing_at(origin, monkeypatch)
    monkeypatch.chdir(tmp_path)
    store = Store(Path("benchmarks"))
    review = FakeReview()
    benchmark = running.start(store, [], stamp="stamp")

    running.review_entry(benchmark, entry_for(origin), store, anonymous, review=review)

    [argv] = review.calls
    sent = Path(argv[argv.index("-o") + 1])
    assert sent.is_absolute()
    assert sent == (tmp_path / "benchmarks" / "runs" / "stamp").absolute()


def test_an_output_flag_of_the_tool_s_own_is_respected(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    review = FakeReview()
    benchmark = running.start(store, ["-o", "/elsewhere"], stamp="stamp")

    running.review_entry(benchmark, entry_for(origin), store, anonymous, review=review)

    [argv] = review.calls
    assert argv.count("-o") == 1
    assert argv[argv.index("-o") + 1] == "/elsewhere"


def test_pointing_the_review_at_another_repository_is_refused_up_front(store: Store) -> None:
    with pytest.raises(ValueError, match="--repo is not a benchmark flag"):
        running.start(store, ["--repo", "/somewhere"], stamp="stamp")
    assert not store.runs.exists()


def test_an_entry_that_cannot_be_fetched_is_reported_and_does_not_stop_the_rest(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    review = FakeReview()
    benchmark = running.start(store, [], stamp="stamp")
    broken = entry_for(origin, id="broken-1", head=MISSING_SHA)

    first = running.review_entry(benchmark, broken, store, anonymous, review=review)
    second = running.review_entry(benchmark, entry_for(origin), store, anonymous, review=review)

    assert first.status == "not_fetched"
    assert MISSING_SHA in first.detail
    assert second.status == "reviewed"
    assert len(review.calls) == 1
    assert not benchmark.ok


def test_a_review_that_did_not_finish_keeps_the_tool_s_exit_code(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    benchmark = running.start(store, [], stamp="stamp")

    outcome = running.review_entry(
        benchmark, entry_for(origin), store, anonymous, review=FakeReview(code=3)
    )

    assert outcome.status == "stopped"
    assert outcome.code == 3
    assert "exited with 3" in outcome.detail
    assert not outcome.crashed, "the tool exited on its own; only a raise is a crash"


def test_a_review_that_raises_is_contained_and_does_not_stop_the_rest(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once, an unhandled timeout out of one entry's review killed the whole
    process and the remaining entries were never reviewed. A raise is that one
    entry's failure, written into the summary like any other."""
    pointing_at(origin, monkeypatch)
    benchmark = running.start(store, [], stamp="stamp")
    other = entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43")

    first = running.review_entry(
        benchmark, entry_for(origin), store, anonymous, review=FakeReview(crash_first=1)
    )
    second = running.review_entry(benchmark, other, store, anonymous, review=FakeReview())

    assert first.status == "stopped"
    assert first.crashed
    assert first.code == -1
    assert first.detail == "roboviewer raised RuntimeError: the provider timed out"
    assert second.status == "reviewed"
    saved = json.loads((store.runs / "stamp" / "summary.json").read_text(encoding="utf-8"))
    assert [row["status"] for row in saved["entries"]] == ["stopped", "reviewed"]
    assert not benchmark.ok


def test_the_summary_says_what_each_review_came_to(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    benchmark = running.start(store, ["--no-judge"], stamp="stamp")
    running.review_entry(benchmark, entry_for(origin), store, anonymous, review=FakeReview())

    summary = running.write_summary(benchmark)

    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert saved["flags"] == ["--no-judge"]
    [row] = saved["entries"]
    assert row["id"] == "sample-42"
    assert row["status"] == "reviewed"
    assert (row["findings"], row["confirmed"], row["out_of_scope"]) == (2, 1, 0)
    assert row["run_id"] == "run-1"
    assert row["directory"].endswith("run-1")
    page = (store.runs / "stamp" / "summary.md").read_text(encoding="utf-8")
    assert "| sample-42 | reviewed | 2 | 1 |" in page
    assert "1 of 1 reviewed" in page


def test_every_review_rewrites_the_summary(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted run keeps the summary of everything it finished, because
    the artifact is updated per review rather than once at the end."""
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    review = FakeReview()
    benchmark = running.start(store, [], repeats=2, stamp="stamp")
    summary = store.runs / "stamp" / "summary.json"

    running.review_entry(benchmark, entry, store, anonymous, review=review)
    after_one = json.loads(summary.read_text(encoding="utf-8"))
    running.review_entry(benchmark, entry, store, anonymous, review=review)
    after_two = json.loads(summary.read_text(encoding="utf-8"))

    assert len(after_one["entries"]) == 1
    assert (store.runs / "stamp" / "summary.md").is_file()
    assert len(after_two["entries"]) == 2
    assert after_two["stats"]["run"]["reviews"] == 2


def test_a_failed_fetch_also_updates_the_summary(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    benchmark = running.start(store, [], stamp="stamp")

    running.review_entry(
        benchmark, entry_for(origin, id="broken-1", head=MISSING_SHA), store, anonymous,
        review=FakeReview(),
    )

    saved = json.loads((store.runs / "stamp" / "summary.json").read_text(encoding="utf-8"))
    [row] = saved["entries"]
    assert row["status"] == "not_fetched"


# ------------------------------------------------------------------ repeats


def test_reviewing_an_entry_again_is_the_next_attempt(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    review = FakeReview()
    benchmark = running.start(store, [], repeats=2, stamp="stamp")

    first = running.review_entry(benchmark, entry, store, anonymous, review=review)
    second = running.review_entry(benchmark, entry, store, anonymous, review=review)

    assert (first.attempt, second.attempt) == (1, 2)
    assert len(review.calls) == 2


def test_zero_repeats_are_refused_before_anything_runs(store: Store) -> None:
    with pytest.raises(ValueError, match="at least once"):
        running.start(store, [], repeats=0, stamp="stamp")
    assert not store.runs.exists()


def test_the_summary_reports_statistics_over_the_repeats(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    review = FakeReview()
    benchmark = running.start(store, [], repeats=2, stamp="stamp")
    running.review_entry(benchmark, entry, store, anonymous, review=review)
    running.review_entry(benchmark, entry, store, anonymous, review=review)

    summary = running.write_summary(benchmark)

    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert saved["repeats"] == 2
    assert [row["attempt"] for row in saved["entries"]] == [1, 2]
    [row, _] = saved["entries"]
    assert row["runner"] == {"prompt_tokens": 100, "completion_tokens": 10, "cached_tokens": 40}
    assert row["judge"] == {"prompt_tokens": 50, "completion_tokens": 5, "cached_tokens": 20}
    [entry_stats] = saved["stats"]["entries"]
    assert entry_stats["title"] == "sample-42"
    assert entry_stats["reviews"] == 2
    assert entry_stats["runner"]["prompt_tokens"] == 200
    assert entry_stats["runner"]["per_review"] == {"mean": 110, "p50": 110, "p90": 110, "max": 110}
    assert entry_stats["judge"]["cached_tokens"] == 40
    # The two repeats confirm the same finding at the same place
    assert entry_stats["consistency"]["all"] == 1.0
    assert entry_stats["consistency"]["by_severity"]["minor"] == 1.0
    assert entry_stats["consistency"]["by_severity"]["blocker"] is None
    assert saved["stats"]["run"]["reviews"] == 2


def test_the_page_shows_one_table_per_entry_and_one_for_the_whole_run(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    review = FakeReview()
    benchmark = running.start(store, [], repeats=2, stamp="stamp")
    running.review_entry(benchmark, entry, store, anonymous, review=review)
    running.review_entry(benchmark, entry, store, anonymous, review=review)

    running.write_summary(benchmark)

    page = (store.runs / "stamp" / "summary.md").read_text(encoding="utf-8")
    assert "Repeats: 2 per entry" in page
    assert "| sample-42 #1 |" in page
    assert "| sample-42 #2 |" in page
    assert "## Statistics" in page
    assert "### sample-42 — 2 review(s)" in page
    assert "### Whole run — 2 review(s)" in page
    assert "| Runner prompt tokens | 200 | | | | |" in page
    assert "| Runner cached tokens | 80 | | | | |" in page
    assert "| Runner tokens per review | | 110 | 110 | 110 | 110 |" in page
    assert "| Judge completion tokens | 10 | | | | |" in page
    assert "| Self-consistency | 1.00 | | | | |" in page
    assert "| Self-consistency, minor | 1.00 | | | | |" in page
    assert "| Self-consistency, blocker | — | | | | |" in page


def test_a_single_review_leaves_the_page_unmarked_and_consistency_unscored(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    benchmark = running.start(store, [], stamp="stamp")
    running.review_entry(benchmark, entry_for(origin), store, anonymous, review=FakeReview())

    running.write_summary(benchmark)

    page = (store.runs / "stamp" / "summary.md").read_text(encoding="utf-8")
    assert "#1" not in page
    assert "Repeats:" not in page
    assert "Self-consistency needs at least two repeats" in page
    assert "| Self-consistency | — | | | | |" in page


# ------------------------------------------------------------------ the command


def index_with(store: Store, *entries: object) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    text = ""
    for entry in entries:
        text += (
            f'[[entry]]\nid = "{entry.id}"\nurl = "{entry.url}"\n'  # type: ignore[attr-defined]
            f'base = "{entry.base}"\nhead = "{entry.head}"\n\n'  # type: ignore[attr-defined]
        )
    store.items.write_text(text, encoding="utf-8")


def test_the_command_reviews_the_index_and_prints_one_line_per_entry(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    review = FakeReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
    )

    code = main(["--root", str(store.root), "run", "--no-judge", "--format", "md"])

    assert code == 0
    assert [argv[9:] for argv in review.calls] == [["--no-judge", "--format", "md"]] * 2
    out = capsys.readouterr().out
    assert "✔ sample-42" in out
    assert "✔ other-43" in out
    assert "2 reviewed, 0 not" in out
    assert "Summary:" in out
    [stamp] = list(store.runs.iterdir())
    assert (stamp / "summary.json").is_file()


def test_entries_narrows_the_run_and_a_double_dash_is_dropped(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    review = FakeReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
    )

    code = main(["--root", str(store.root), "run", "--entries", "other-43", "--", "-v"])

    assert code == 0
    [argv] = review.calls
    assert argv[5:7] == ["--repo", str(store.repo_dir(entry_for(origin, id="other-43")))]
    assert argv[9:] == ["-v"]
    capsys.readouterr()


def test_the_command_exits_non_zero_when_an_entry_was_not_reviewed(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", FakeReview())
    index_with(store, entry_for(origin, head=MISSING_SHA))

    code = main(["--root", str(store.root), "run"])

    assert code == 1
    said = capsys.readouterr()
    assert "not fetched" in said.err
    assert "0 reviewed, 1 not" in said.out


def test_the_command_repeats_every_entry_and_counts_the_attempts(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    review = FakeReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
    )

    code = main(["--root", str(store.root), "run", "--repeats", "2"])

    assert code == 0
    assert len(review.calls) == 4
    out = capsys.readouterr().out
    assert "2 entr(ies) x 2" in out
    assert "── sample-42  1/2" in out
    assert "── sample-42  2/2" in out
    [stamp] = list(store.runs.iterdir())
    saved = json.loads((stamp / "summary.json").read_text(encoding="utf-8"))
    assert [(row["id"], row["attempt"]) for row in saved["entries"]] == [
        ("sample-42", 1), ("sample-42", 2), ("other-43", 1), ("other-43", 2),
    ]


def test_an_entry_that_cannot_be_fetched_is_not_asked_again_by_the_repeats(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", FakeReview())
    index_with(store, entry_for(origin, head=MISSING_SHA))

    code = main(["--root", str(store.root), "run", "--repeats", "3"])

    assert code == 1
    assert capsys.readouterr().err.count("not fetched") == 1
    [stamp] = list(store.runs.iterdir())
    saved = json.loads((stamp / "summary.json").read_text(encoding="utf-8"))
    assert [row["status"] for row in saved["entries"]] == ["not_fetched"]


def test_a_crashing_entry_skips_its_repeats_and_the_next_entry_still_runs(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same review would raise the same way, so the crashed entry is not
    asked again — but the crash stays its own, and the next entry gets its
    full share of attempts."""
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    review = FakeReview(crash_first=1)
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
    )

    code = main(["--root", str(store.root), "run", "--repeats", "2"])

    assert code == 1
    assert len(review.calls) == 3, "the crashed entry is not asked again; the next one is"
    said = capsys.readouterr()
    assert "raised RuntimeError" in said.err
    assert "2 reviewed, 1 not" in said.out
    [stamp] = list(store.runs.iterdir())
    saved = json.loads((stamp / "summary.json").read_text(encoding="utf-8"))
    assert [(row["id"], row["attempt"], row["status"]) for row in saved["entries"]] == [
        ("sample-42", 1, "stopped"), ("other-43", 1, "reviewed"), ("other-43", 2, "reviewed"),
    ]


def test_a_rate_limit_still_stops_the_whole_run(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A crash is one entry's failure; a rate limit is not — every fetch after
    it would be refused the same way, so the run stops asking."""
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr("roboviewer.benchmark.github._urllib_transport", rate_limited)
    review = FakeReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
    )

    code = main(["--root", str(store.root), "run"])

    assert code == 1
    assert review.calls == [], "nothing was reviewed: the first fetch already hit the limit"
    assert "rate limit reached" in capsys.readouterr().err


def test_the_command_refuses_zero_repeats(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    review = FakeReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(store, entry_for(origin))

    assert main(["--root", str(store.root), "run", "--repeats", "0"]) == 2
    assert review.calls == []
    assert "at least once" in capsys.readouterr().err


class ThreadAwareReview(FakeReview):
    """Remembers which thread reviewed which clone, so a test can pin that one
    entry's repeats never leave their worker."""

    def __init__(self, code: int = 0) -> None:
        super().__init__(code)
        self.threads: dict[str, set[int]] = {}

    def __call__(self, argv: list[str], observer) -> int:  # type: ignore[no-untyped-def]
        clone = Path(argv[argv.index("--repo") + 1]).name
        self.threads.setdefault(clone, set()).add(threading.get_ident())
        return super().__call__(argv, observer)


def test_parallel_reviews_every_entry_and_keeps_an_entry_on_one_worker(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    review = ThreadAwareReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
        entry_for(origin, id="third-44", url="https://github.com/owner/repo/pull/44"),
    )

    code = main(["--root", str(store.root), "run", "--parallel", "2", "--repeats", "2"])

    assert code == 0
    assert len(review.calls) == 6
    for clone, threads in review.threads.items():
        assert len(threads) == 1, f"{clone}: repeats crossed workers"
    [stamp] = list(store.runs.iterdir())
    saved = json.loads((stamp / "summary.json").read_text(encoding="utf-8"))
    assert len(saved["entries"]) == 6
    by_id: dict[str, list[int]] = {}
    for row in saved["entries"]:
        by_id.setdefault(row["id"], []).append(row["attempt"])
    assert all(sorted(attempts) == [1, 2] for attempts in by_id.values())
    out = capsys.readouterr().out
    assert any(line.startswith("sample-42") and "| ──" in line for line in out.splitlines())
    assert any(line.startswith("third-44") for line in out.splitlines())


def test_parallel_below_one_is_refused(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    review = FakeReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(store, entry_for(origin))

    assert main(["--root", str(store.root), "run", "--parallel", "0"]) == 2
    assert review.calls == []
    assert "at least one" in capsys.readouterr().err


def test_a_setup_failure_stops_a_parallel_run_before_the_queue_drains(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    review = FakeReview(code=2)
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
        entry_for(origin, id="third-44", url="https://github.com/owner/repo/pull/44"),
    )

    code = main(["--root", str(store.root), "run", "--parallel", "2"])

    assert code == 1
    assert len(review.calls) == 2, "the queued entry must never start"
    assert "could not start" in capsys.readouterr().err


def test_a_repository_flag_is_refused_before_anything_runs(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    review = FakeReview()
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(store, entry_for(origin))

    assert main(["--root", str(store.root), "run", "--repo", "/elsewhere"]) == 2
    assert review.calls == []
    assert "not a benchmark flag" in capsys.readouterr().err


def test_a_setup_failure_stops_the_run_instead_of_failing_every_entry(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 2 is "the tool could not start" — a missing key, a broken config.
    That is about the machine, not the entry, and without the stop each of a
    dozen entries would be cloned and then refused identically."""
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    review = FakeReview(code=2)
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", review)
    index_with(
        store,
        entry_for(origin),
        entry_for(origin, id="other-43", url="https://github.com/owner/repo/pull/43"),
    )

    code = main(["--root", str(store.root), "run"])

    assert code == 1
    assert len(review.calls) == 1, "the second entry was never cloned or reviewed"
    said = capsys.readouterr()
    assert "could not start" in said.err
    assert "--entries sample-42" in said.err
    assert "0 reviewed, 1 not" in said.out


def test_the_run_says_it_is_cloning_before_the_silence(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(
        "roboviewer.benchmark.github._urllib_transport", transport_for(REST_COMMENTS)
    )
    monkeypatch.setattr("roboviewer.benchmark.cli.review_main", FakeReview())
    index_with(store, entry_for(origin))

    main(["--root", str(store.root), "run"])
    first = capsys.readouterr().out
    main(["--root", str(store.root), "run"])
    second = capsys.readouterr().out

    assert "cloning" in first
    assert "cloning" not in second, "the clone is already there; nothing to warn about"
