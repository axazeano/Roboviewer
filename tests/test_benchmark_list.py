"""`benchmark list add|remove|show`: the index edited from the command line.

`add` is the one that talks to GitHub, and what it has to get right is the
head: the commit reviewers saw, not the branch tip, whenever the review names
one. The transport answers the two REST calls `add` makes and nothing else, so
a test that reached for anything more would fail rather than go to the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from roboviewer.benchmark import github as github_module
from roboviewer.benchmark.cli import main
from roboviewer.benchmark.github import GitHub, Response
from roboviewer.benchmark.items import load_items

from .test_benchmark_fetch import Origin, make_origin, pointing_at

PULL_URL = "https://github.com/owner/repo/pull/42"


def pull_facts(origin: Origin, *, head: str | None = None) -> dict[str, Any]:
    return {
        "html_url": PULL_URL,
        "number": 42,
        "changed_files": 1,
        "additions": 1,
        "deletions": 0,
        "base": {
            "sha": origin.base,
            "repo": {
                "stargazers_count": 1200,
                "language": "Python",
                "license": {"spdx_id": "MIT"},
            },
        },
        "head": {"sha": head or origin.head},
    }


def rest_comment(commit: str) -> dict[str, Any]:
    return {
        "id": 1,
        "path": "cart.py",
        "original_line": 2,
        "original_commit_id": commit,
        "body": "This discards the second line.",
        "user": {"login": "reviewer"},
        "created_at": "2026-05-01T10:00:00Z",
        "html_url": f"{PULL_URL}#discussion_r1",
    }


def github_answering(facts: dict[str, Any], comments: list[dict[str, Any]]) -> GitHub:
    def transport(url: str, headers: dict[str, str], body: bytes | None) -> Response:
        if url.endswith("/pulls/42"):
            return 200, {}, json.dumps(facts).encode()
        if "/pulls/42/comments" in url:
            return 200, {}, json.dumps(comments).encode()
        raise AssertionError(f"unexpected request: {url}")

    return GitHub(token=None, transport=transport)


@pytest.fixture
def origin(tmp_path: Path) -> Origin:
    return make_origin(tmp_path / "origin")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "benchmarks"


def use(monkeypatch: pytest.MonkeyPatch, github: GitHub) -> None:
    monkeypatch.setattr("roboviewer.benchmark.cli.GitHub", lambda token: github)  # noqa: ARG005


# ------------------------------------------------------------------ add


def test_add_writes_the_entry_and_clones_it(
    origin: Origin, root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pointing_at(origin, monkeypatch)
    use(monkeypatch, github_answering(pull_facts(origin), [rest_comment(origin.head)]))

    code = main(["--root", str(root), "list", "add", PULL_URL])

    assert code == 0
    [entry] = load_items(root / "items.toml")
    assert (entry.id, entry.base, entry.head) == ("repo-42", origin.base, origin.head)
    assert (entry.language, entry.license, entry.files) == ("Python", "MIT", 1)
    assert (root / "repos" / "repo-42" / ".git").is_dir()
    assert (root / "comments" / "repo-42.json").is_file()
    out = capsys.readouterr().out
    assert "added to" in out
    assert "cloning" in out  # said before the silence of a real clone
    assert "roboviewer" in out  # how to review it by hand


def test_add_takes_the_head_reviewers_saw_not_the_branch_tip(
    origin: Origin, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API hands out the tip of the branch, which on any review the author
    answered is the commit after the fixes."""
    pointing_at(origin, monkeypatch)
    tip = "f" * 40
    use(monkeypatch, github_answering(pull_facts(origin, head=tip), [rest_comment(origin.head)]))

    main(["--root", str(root), "list", "add", PULL_URL, "--no-fetch"])

    [entry] = load_items(root / "items.toml")
    assert entry.head == origin.head


def test_add_falls_back_to_the_tip_and_says_so_when_no_thread_names_a_commit(
    origin: Origin, root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, github_answering(pull_facts(origin), []))

    code = main(["--root", str(root), "list", "add", PULL_URL, "--no-fetch"])

    assert code == 0
    [entry] = load_items(root / "items.toml")
    assert entry.head == origin.head
    assert "No review thread names this head" in capsys.readouterr().err


def test_add_refuses_a_pull_request_already_listed(
    origin: Origin, root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use(monkeypatch, github_answering(pull_facts(origin), [rest_comment(origin.head)]))
    main(["--root", str(root), "list", "add", PULL_URL, "--no-fetch"])

    code = main(["--root", str(root), "list", "add", PULL_URL, "--no-fetch"])

    assert code == 2
    assert "already in the index" in capsys.readouterr().err
    assert len(load_items(root / "items.toml")) == 1


def test_add_refuses_what_is_not_a_pull_request_url(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--root", str(root), "list", "add", "https://github.com/owner/repo"]) == 2
    assert "not a GitHub pull request URL" in capsys.readouterr().err
    assert not (root / "items.toml").exists()


def test_a_failed_clone_keeps_the_entry_and_exits_non_zero(
    origin: Origin, root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pointing_at(origin, monkeypatch)
    missing = "d" * 40
    use(
        monkeypatch,
        github_answering(pull_facts(origin, head=missing), [rest_comment(missing)]),
    )

    code = main(["--root", str(root), "list", "add", PULL_URL])

    assert code == 1
    assert len(load_items(root / "items.toml")) == 1, "`fetch` can try again later"
    assert missing in capsys.readouterr().err


# ------------------------------------------------------------------ remove and show


def test_remove_takes_the_entry_out_and_leaves_the_clone_for_the_person(
    origin: Origin, root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pointing_at(origin, monkeypatch)
    use(monkeypatch, github_answering(pull_facts(origin), [rest_comment(origin.head)]))
    main(["--root", str(root), "list", "add", PULL_URL])
    capsys.readouterr()

    code = main(["--root", str(root), "list", "remove", "repo-42"])

    assert code == 0
    assert load_items(root / "items.toml", allow_empty=True) == []
    assert (root / "repos" / "repo-42").is_dir()
    assert "delete it when you are done" in capsys.readouterr().out


def test_remove_of_an_unknown_entry_is_a_setup_error(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root.mkdir()
    (root / "items.toml").write_text(
        f'[[entry]]\nid = "a"\nurl = "{PULL_URL}"\nbase = "{"1" * 40}"\nhead = "{"2" * 40}"\n',
        encoding="utf-8",
    )

    assert main(["--root", str(root), "list", "remove", "nope"]) == 2
    assert "no entry with id or url" in capsys.readouterr().err


def test_show_prints_each_entry_and_whether_it_is_on_disk(
    origin: Origin, root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pointing_at(origin, monkeypatch)
    use(monkeypatch, github_answering(pull_facts(origin), [rest_comment(origin.head)]))
    main(["--root", str(root), "list", "add", PULL_URL, "--no-fetch"])
    capsys.readouterr()

    assert main(["--root", str(root), "list", "show"]) == 0
    shown = capsys.readouterr().out
    assert "○ repo-42" in shown
    assert PULL_URL in shown

    main(["--root", str(root), "fetch"])
    capsys.readouterr()
    assert main(["--root", str(root), "list", "show"]) == 0
    assert "• repo-42" in capsys.readouterr().out


def test_show_on_an_empty_root_says_how_to_start(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--root", str(root), "list", "show"]) == 0
    assert "benchmark list add" in capsys.readouterr().out


# ------------------------------------------------------------------ the facts themselves


def test_the_facts_come_off_the_pull_request_as_rest_describes_it(origin: Origin) -> None:
    github = github_answering(pull_facts(origin), [])

    facts = github.pull_request(github_module.PullRequest("owner", "repo", 42))

    assert facts.base == origin.base
    assert facts.head == origin.head
    assert (facts.files, facts.added, facts.removed) == (1, 1, 0)
    assert (facts.stars, facts.language, facts.license) == (1200, "Python", "MIT")
