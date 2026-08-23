"""Building an entry, on local repositories and a transport that never connects.

The claim being tested is the one the benchmark rests on: after the command runs,
`roboviewer <base> <head>` in the built directory reviews the code reviewers
were looking at. So the check is not that files appeared — it is that the tool's
own diff collector, pointed at the result, sees the change the pull request made.

The origin repositories here are ordinary local ones. `git fetch` against a path
exercises the same code as a fetch against github.com, without the network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from roboviewer import repo
from roboviewer.benchmark import clone
from roboviewer.benchmark import github as github_module
from roboviewer.benchmark.cli import _entry_line, main
from roboviewer.benchmark.fetch import fetch
from roboviewer.benchmark.github import GitHub, RateLimited, Response
from roboviewer.benchmark.items import Entry
from roboviewer.benchmark.store import Store, default_root

MISSING_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

REST_COMMENTS: list[dict[str, Any]] = [
    {
        "id": 1,
        "path": "cart.py",
        "original_line": 2,
        "body": "This discards the second line.",
        "user": {"login": "reviewer"},
        "created_at": "2026-05-01T10:00:00Z",
        "html_url": "https://github.com/owner/repo/pull/42#discussion_r1",
    }
]

GRAPHQL_THREADS: dict[str, Any] = {
    "data": {
        "repository": {
            "pullRequest": {
                "reviewThreads": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "isResolved": True,
                            "path": "cart.py",
                            "line": 9,
                            "originalLine": 2,
                            "comments": {
                                "nodes": [
                                    {
                                        "author": {"login": "reviewer"},
                                        "body": "This discards the second line.",
                                        "createdAt": "2026-05-01T10:00:00Z",
                                        "url": "https://github.com/owner/repo/pull/42#r1",
                                    }
                                ]
                            },
                        }
                    ],
                }
            }
        }
    }
}


@dataclass
class Origin:
    """A repository shaped like a pull request: a base on the target branch, a
    head on the branch under review, and their branch point behind both."""

    path: Path
    base: str
    head: str


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def make_origin(path: Path) -> Origin:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "T")
    (path / "cart.py").write_text("one\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "root")

    git(path, "checkout", "-q", "-b", "pr")
    (path / "cart.py").write_text("one\ntwo\n", encoding="utf-8")
    git(path, "commit", "-qam", "the change under review")
    head = git(path, "rev-parse", "HEAD")

    git(path, "checkout", "-q", "main")
    (path / "unrelated.py").write_text("elsewhere\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "someone else's commit, landed after the branch point")
    base = git(path, "rev-parse", "HEAD")

    # What github.com serves and a bare local repository does not
    git(path, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(path, "update-ref", "refs/pull/42/head", head)
    return Origin(path=path, base=base, head=head)


def entry_for(origin: Origin, **overrides: Any) -> Entry:
    fields: dict[str, Any] = {
        "id": "sample-42",
        "url": "https://github.com/owner/repo/pull/42",
        "base": origin.base,
        "head": origin.head,
    }
    fields.update(overrides)
    return Entry(**fields)


def transport_for(answer: Any) -> Any:
    def transport(url: str, headers: dict[str, str], body: bytes | None) -> Response:
        return 200, {}, json.dumps(answer).encode()

    return transport


def offline(url: str, headers: dict[str, str], body: bytes | None) -> Response:
    raise AssertionError(f"the network was used: {url}")


def rate_limited(url: str, headers: dict[str, str], body: bytes | None) -> Response:
    return 403, {"x-ratelimit-remaining": "0"}, b"{}"


@pytest.fixture(autouse=True)
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer running these has a token in the environment, and it would
    send every one of them down the GraphQL path."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return make_origin(tmp_path / "origin")


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "benchmarks")


@pytest.fixture
def anonymous() -> GitHub:
    return GitHub(token=None, transport=transport_for(REST_COMMENTS))


def pointing_at(origin: Origin, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send the clone URL derived from the pull request URL to a local path."""
    monkeypatch.setattr(
        "roboviewer.benchmark.items.PullRequest.clone_url",
        property(lambda self: str(origin.path)),  # noqa: ARG005 — the property signature
    )


# ------------------------------------------------------------------ what the entry is for


def test_the_built_entry_is_a_repository_roboviewer_reviews(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)

    result = fetch(entry, store, anonymous)

    assert result.status == "built"
    bundle = repo.collect(
        store.repo_dir(entry),
        entry.base,
        entry.head,
        budget=repo.ContextBudget(
            context_lines=3, max_chars=10_000, inline_max_lines=100,
            inline_max_total_chars=10_000,
        ),
        excludes=[],
        resolve_references=False,
    )

    # The change under review, diffed from the branch point rather than from the
    # base commit — the same thing a reviewer on the pull request page saw
    assert [stat.file for stat in bundle.files] == ["cart.py"]
    assert "two" in bundle.attachments.annotated


def test_the_head_reviewers_saw_is_what_is_checked_out(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)

    fetch(entry, store, anonymous)

    repo = store.repo_dir(entry)
    assert git(repo, "rev-parse", "HEAD") == origin.head
    assert (repo / "cart.py").read_text(encoding="utf-8") == "one\ntwo\n"


# ------------------------------------------------------------------ the comments beside it


def test_the_comments_are_saved_with_file_line_author_body_and_resolution(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    with_token = GitHub(token="t", transport=transport_for(GRAPHQL_THREADS))

    fetch(entry, store, with_token)

    saved = json.loads(store.comments_path(entry).read_text(encoding="utf-8"))
    assert saved["resolution"] == "known"
    thread = saved["threads"][0]
    assert thread["file"] == "cart.py"
    assert thread["line"] == 2
    assert thread["resolved"] is True
    assert thread["comments"][0]["author"] == "reviewer"
    assert thread["comments"][0]["body"] == "This discards the second line."


def rest_comment_on(commit: str) -> list[dict[str, Any]]:
    return [{**REST_COMMENTS[0], "original_commit_id": commit}]


def test_the_commit_a_thread_was_written_against_is_saved(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    github = GitHub(transport=transport_for(rest_comment_on(origin.head)))

    fetch(entry, store, github)

    saved = json.loads(store.comments_path(entry).read_text(encoding="utf-8"))
    assert saved["threads"][0]["commit"] == origin.head


def test_a_head_the_review_was_written_against_raises_nothing(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    github = GitHub(transport=transport_for(rest_comment_on(origin.head)))

    assert fetch(entry, store, github).reviewed_head == ""


def test_a_head_past_the_review_is_named_rather_than_built_quietly(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """The pull request API hands out the branch tip, which is the commit after
    the author fixed what reviewers found. An entry there measures nothing, and
    nothing else in the build can tell that from a correct head."""
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    github = GitHub(transport=transport_for(rest_comment_on(MISSING_SHA)))

    result = fetch(entry, store, github)

    assert result.status == "built"
    assert result.reviewed_head == MISSING_SHA
    _entry_line(result, github)
    assert MISSING_SHA[:12] in capsys.readouterr().err


def test_a_review_spanning_rounds_keeps_a_head_from_any_of_them(
    origin: Origin, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comments land on several commits when a branch is pushed to twice. The
    head only has to be one of them — picking the later round is a choice, not
    a mistake."""
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    rounds = [
        {**REST_COMMENTS[0], "id": 1, "original_commit_id": MISSING_SHA},
        {**REST_COMMENTS[0], "id": 2, "original_commit_id": origin.head},
        {**REST_COMMENTS[0], "id": 3, "original_commit_id": MISSING_SHA},
    ]

    assert fetch(entry, store, GitHub(transport=transport_for(rounds))).reviewed_head == ""


def test_comments_fetched_without_a_token_say_resolution_is_unknown(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)

    fetch(entry, store, anonymous)

    saved = json.loads(store.comments_path(entry).read_text(encoding="utf-8"))
    assert saved["resolution"] == "unknown"
    assert saved["threads"][0]["resolved"] is None


# ------------------------------------------------------------------ the second run


def test_a_rerun_on_an_unchanged_entry_does_no_network_work(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    fetch(entry, store, anonymous)

    # Both doors shut: the transport fails the test if it is called, and the
    # repository the clone came from is gone
    shutil.rmtree(origin.path)
    result = fetch(entry, store, GitHub(token=None, transport=offline))

    assert result.status == "cached"


def test_an_entry_whose_head_changed_in_the_list_is_built_again(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    fetch(entry_for(origin), store, anonymous)

    # The same id, a different head: the marker is what notices
    corrected = entry_for(origin, head=git(origin.path, "rev-parse", "main"))
    assert store.is_built(corrected) is False

    assert fetch(corrected, store, anonymous).status == "built"


def test_a_clone_deleted_by_hand_is_rebuilt_rather_than_trusted(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    fetch(entry, store, anonymous)
    shutil.rmtree(store.repo_dir(entry))

    assert store.is_built(entry) is False
    assert fetch(entry, store, anonymous).status == "built"


# ------------------------------------------------------------------ when an entry cannot be built


def test_a_head_the_repository_does_not_have_names_it(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin, head=MISSING_SHA)

    result = fetch(entry, store, anonymous)

    assert result.status == "failed"
    assert MISSING_SHA in result.detail
    assert "force-pushed" in result.detail


def test_a_failed_entry_leaves_nothing_behind(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin, head=MISSING_SHA)

    fetch(entry, store, anonymous)

    assert not store.repo_dir(entry).exists()
    assert list((store.root / "repos" / ".building").iterdir()) == []


def test_a_refresh_that_is_rate_limited_keeps_what_was_already_built(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    fetch(entry, store, anonymous)

    with pytest.raises(RateLimited):
        fetch(entry, store, GitHub(token=None, transport=rate_limited), refresh=True)

    # The promise the rate-limit message makes: what is already fetched is kept
    assert store.is_built(entry)


# ------------------------------------------------------------------ fetching the commits


def test_a_head_no_branch_points_at_is_fetched_through_the_pull_ref(
    origin: Origin, tmp_path: Path
) -> None:
    # A repository that refuses to serve a bare SHA, which is every git server
    # that has not opted in — the fallback is the only way in
    git(origin.path, "config", "uploadpack.allowAnySHA1InWant", "false")
    branch_point = git(origin.path, "merge-base", "main", "pr")
    git(origin.path, "branch", "-q", "-D", "pr")

    repo = tmp_path / "clone"
    clone.prepare(repo, str(origin.path), branch_point, origin.head,
                  fallback_refs=["refs/pull/42/head"])

    assert clone.has_commits(repo, branch_point, origin.head)


def test_the_fetched_commits_survive_a_garbage_collection(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)
    fetch(entry, store, anonymous)

    git(store.repo_dir(entry), "gc", "--prune=now", "--quiet")

    assert clone.has_commits(store.repo_dir(entry), entry.base, entry.head)


# ------------------------------------------------------------------ where the benchmarks live


def test_the_root_is_benchmarks_where_the_command_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOVIEWER_BENCHMARKS", raising=False)

    assert default_root() == Path("benchmarks")


def test_the_environment_moves_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOVIEWER_BENCHMARKS", str(tmp_path / "elsewhere"))

    assert default_root() == tmp_path / "elsewhere"


def test_a_clone_is_the_entry_s_directory_and_the_marker_hides_in_its_git_dir(
    origin: Origin, store: Store, anonymous: GitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    pointing_at(origin, monkeypatch)
    entry = entry_for(origin)

    fetch(entry, store, anonymous)

    assert store.repo_dir(entry) == store.root / "repos" / "sample-42"
    assert (store.repo_dir(entry) / ".git" / "benchmark.json").is_file()
    assert git(store.repo_dir(entry), "status", "--porcelain") == ""
    assert store.comments_path(entry) == store.root / "comments" / "sample-42.json"


# ------------------------------------------------------------------ the command


def root_with(tmp_path: Path, origin: Origin, **overrides: Any) -> Path:
    """A benchmarks directory whose index names the origin."""
    entry = entry_for(origin, **overrides)
    root = tmp_path / "benchmarks"
    root.mkdir()
    path = root / "items.toml"
    path.write_text(
        f'[[entry]]\nid = "{entry.id}"\nurl = "{entry.url}"\n'
        f'base = "{entry.base}"\nhead = "{entry.head}"\n',
        encoding="utf-8",
    )
    return root


def test_the_command_fetches_the_index_and_says_where_it_went(
    tmp_path: Path, origin: Origin, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(github_module, "_urllib_transport", transport_for(REST_COMMENTS))
    root = root_with(tmp_path, origin)

    code = main(["--root", str(root), "fetch"])

    assert code == 0
    out = capsys.readouterr().out
    assert str(root) in out
    assert "roboviewer" in out  # the line telling you how to review what was built
    assert (root / "comments" / "sample-42.json").is_file()


def test_the_command_exits_non_zero_when_an_entry_could_not_be_built(
    tmp_path: Path, origin: Origin, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(github_module, "_urllib_transport", transport_for(REST_COMMENTS))

    code = main(["--root", str(root_with(tmp_path, origin, head=MISSING_SHA)), "fetch"])

    assert code == 1
    assert MISSING_SHA in capsys.readouterr().err


def test_the_command_stops_on_the_rate_limit_and_says_that_is_why(
    tmp_path: Path, origin: Origin, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(github_module, "_urllib_transport", rate_limited)

    code = main(["--root", str(root_with(tmp_path, origin)), "fetch"])

    assert code == 1
    err = capsys.readouterr().err
    assert "rate limit" in err
    assert "GITHUB_TOKEN" in err


def test_a_list_that_does_not_parse_stops_the_command_before_anything_is_fetched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "benchmarks"
    root.mkdir()
    (root / "items.toml").write_text('[[entry]]\nid = "x"\n', encoding="utf-8")

    # 2 is "the tool could not run", the same code roboviewer uses
    assert main(["--root", str(root), "fetch"]) == 2
    assert "Index error" in capsys.readouterr().err


def test_entries_fetches_only_the_named_entry(
    tmp_path: Path, origin: Origin, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pointing_at(origin, monkeypatch)
    monkeypatch.setattr(github_module, "_urllib_transport", transport_for(REST_COMMENTS))
    root = root_with(tmp_path, origin)

    code = main(["--root", str(root), "fetch", "--entries", "sample-42"])

    assert code == 0
    capsys.readouterr()
    assert (root / "repos" / "sample-42" / ".git").is_dir()
