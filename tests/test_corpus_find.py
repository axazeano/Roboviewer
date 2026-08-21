"""Sieving GitHub for corpus candidates, without going near GitHub.

What matters here is what a wrong answer would cost. A size filter that leaks
puts a two-file change in a list meant to measure large ones. A head taken from
the wrong end of the review puts the entry at the commit where every defect is
already fixed, and then no run can ever hit it — the mistake TASK-18 wrote down.
And a truncated search that says nothing reads as "GitHub has no more", which is
how a corpus quietly ends up drawn from one week of one year.
"""

from __future__ import annotations

import json
from typing import Any

from corpus.cli import main
from corpus.find import Candidate, as_toml, propose_head, search
from corpus.github import GitHub, Response


def node(
    number: int,
    *,
    files: int = 10,
    threads: int = 2,
    stars: int = 100,
    added: int = 1,
    removed: int = 1,
) -> dict[str, Any]:
    return {
        "url": f"https://github.com/acme/widget/pull/{number}",
        "number": number,
        "changedFiles": files,
        "additions": added,
        "deletions": removed,
        "baseRefOid": "b" * 40,
        "reviewThreads": {"totalCount": threads},
        "repository": {
            "nameWithOwner": "acme/widget",
            "stargazerCount": stars,
            "primaryLanguage": {"name": "Go"},
            "licenseInfo": {"spdxId": "MIT"},
        },
    }


def page(*nodes: dict[str, Any], more: bool = False, matched: int = 2) -> dict[str, Any]:
    return {
        "data": {
            "search": {
                "issueCount": matched,
                "pageInfo": {"hasNextPage": more, "endCursor": "cursor"},
                "nodes": list(nodes),
            }
        }
    }


class Answers:
    """A transport that reads from a script and remembers the bodies it was sent."""

    def __init__(self, *answers: Any):
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes | None  # noqa: ARG002
    ) -> Response:
        self.bodies.append(json.loads(body) if body else {})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return 200, {}, json.dumps(answer).encode()


def client(*answers: Any) -> tuple[GitHub, Answers]:
    transport = Answers(*answers)
    return GitHub(token="t", transport=transport), transport


# ------------------------------------------------------------------ the sieve


def test_the_size_filter_is_the_whole_point_and_it_keeps_the_boundary() -> None:
    github, _ = client(page(node(1, files=29), node(2, files=30), node(3, files=31)))

    result = search(github, "q", min_files=30)

    assert [c.number for c in result.candidates] == [2, 3]


def test_a_pull_request_nobody_reviewed_a_line_of_is_never_a_candidate() -> None:
    """Threads are what the criteria are judged on; zero of them means there is
    nothing to judge, whatever the diff looks like."""
    github, _ = client(page(node(1, threads=0), node(2, threads=1)))

    result = search(github, "q")

    assert [c.number for c in result.candidates] == [2]


def test_stars_filter_and_defaults_leave_a_bare_query_unfiltered() -> None:
    github, _ = client(page(node(1, stars=10), node(2, stars=1000)))
    assert [c.number for c in search(github, "q", min_stars=500).candidates] == [2]

    github, _ = client(page(node(1, stars=10), node(2, stars=1000)))
    assert len(search(github, "q").candidates) == 2


def test_paging_follows_the_cursor_and_stops_at_the_page_budget() -> None:
    github, transport = client(
        page(node(1), more=True),
        page(node(2), more=True),
        page(node(3), more=True),
    )

    result = search(github, "q", pages=2)

    assert [c.number for c in result.candidates] == [1, 2]
    assert len(transport.bodies) == 2
    assert transport.bodies[0]["variables"]["cursor"] is None
    assert transport.bodies[1]["variables"]["cursor"] == "cursor"


def test_paging_stops_when_github_says_there_is_no_more() -> None:
    github, transport = client(page(node(1), more=False))

    search(github, "q", pages=5)

    assert len(transport.bodies) == 1


def test_the_thousand_result_ceiling_is_told_apart_from_a_short_page_budget() -> None:
    """Two different situations, two different fixes: narrow the query, or read
    further. Reporting both as one leaves the caller guessing."""
    github, _ = client(page(*[node(n) for n in range(50)], more=True, matched=50_000))
    budget = search(github, "q", pages=1)
    assert budget.stopped_early and not budget.truncated

    github, _ = client(page(*[node(n) for n in range(50)], more=True, matched=50_000))
    ceiling = search(github, "q", pages=20)
    assert ceiling.truncated and not ceiling.stopped_early


def test_everything_read_is_reported_even_when_nothing_passes() -> None:
    github, _ = client(page(node(1, files=2), matched=7))

    result = search(github, "q", min_files=30)

    assert result.candidates == []
    assert (result.matched, result.scanned) == (7, 1)


# -------------------------------------------------------------------- the head


def graphql_threads(*threads: tuple[str, str, str]) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "isResolved": False,
                                "path": "a.go",
                                "line": 1,
                                "originalLine": 1,
                                "comments": {
                                    "nodes": [
                                        {
                                            "author": {"login": "reviewer"},
                                            "body": body,
                                            "createdAt": when,
                                            "url": "https://github.com/x#r1",
                                            "originalCommit": {"oid": commit},
                                        }
                                    ]
                                },
                            }
                            for commit, when, body in threads
                        ],
                    }
                }
            }
        }
    }


CANDIDATE = Candidate(
    url="https://github.com/acme/widget/pull/7",
    slug="acme/widget",
    number=7,
    files=30,
    added=100,
    removed=5,
    threads=2,
    stars=900,
    language="Go",
    license="MIT",
    base="b" * 40,
)


def test_the_head_is_the_commit_the_earliest_thread_was_written_against() -> None:
    """A review over several rounds anchors later threads at later commits. The
    last of them is the state after the fixes, where nothing is left to find."""
    github, _ = client(
        graphql_threads(
            ("f" * 40, "2026-05-02T10:00:00Z", "still wrong"),
            ("e" * 40, "2026-05-01T10:00:00Z", "this races"),
        )
    )

    head, threads = propose_head(github, CANDIDATE)

    assert head == "e" * 40
    assert len(threads) == 2


def test_a_review_with_no_commit_recorded_gives_no_head_rather_than_a_guess() -> None:
    github, _ = client(graphql_threads(("", "2026-05-01T10:00:00Z", "naming")))

    head, threads = propose_head(github, CANDIDATE)

    assert head == ""
    assert len(threads) == 1, "the threads still come back: they are what a person judges"


def test_the_stanza_carries_the_facts_and_leaves_the_judgement_blank() -> None:
    stanza = as_toml(CANDIDATE, "e" * 40)

    assert 'id = "widget-7"' in stanza
    assert f'head = "{"e" * 40}"' in stanza
    assert 'found = ""' in stanza, "what the review found is a person's sentence, not a guess"
    assert 'domain = ""' in stanza


def test_the_stanza_parses_as_the_entry_the_fetcher_already_loads() -> None:
    import tomllib

    from corpus.entries import Entry

    raw = tomllib.loads(as_toml(CANDIDATE, "e" * 40))

    entry = Entry.model_validate(raw["entry"][0])
    assert entry.pull.number == 7
    assert entry.files == 30


# ------------------------------------------------------------------ the command


def test_find_needs_a_token_and_says_so_instead_of_failing_at_the_wire(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    assert main(["find", "is:pr"]) == 2


def test_the_build_command_is_untouched_by_the_new_one(tmp_path, capsys) -> None:
    """`find` is recognised by that word alone; a path first still builds."""
    missing = tmp_path / "no-such-list.toml"

    assert main([str(missing)]) == 2
    assert "Corpus list error" in capsys.readouterr().err
