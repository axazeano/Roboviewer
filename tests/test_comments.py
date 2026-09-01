"""Putting a finished review on a pull request.

What is pinned down here is the part that is easy to get quietly wrong. That a
finding the diff cannot carry ends up in the body instead of being dropped —
the scope gate allows a margin around a changed line and a forge allows none, so
the two disagree by design. That a rejected finding never reaches somebody's
pull request. That every way the forge can refuse ends as a sentence naming what
to fix rather than as a traceback. And that a dry run sends nothing at all.

No test here reaches the network: the forge takes its transport.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from roboviewer.cli import main
from roboviewer.comments import compose, detect, on_github, pull_request, token_for
from roboviewer.comments.compose import SIGNATURE
from roboviewer.comments.forge import ForgeError, forge_for
from roboviewer.comments.github import GitHubForge
from roboviewer.models import DiffStat, Finding, ReviewRun, Severity, Verdict

ACTIONS = {
    "GITHUB_REPOSITORY": "acme/app",
    "GITHUB_REF": "refs/pull/42/merge",
}


def make_run(findings: list[Finding], verdicts: dict[str, str] | None = None) -> ReviewRun:
    return ReviewRun(
        run_id="20260831-120000",
        repo_root="/tmp/repo",
        branch="feature/cart",
        target="develop",
        base_sha="a" * 40,
        head_sha="b" * 40,
        model="test-model",
        started_at="2026-08-31T12:00:00Z",
        judge_summary="Two things worth fixing.",
        files=[DiffStat(file="cart.py", status="M", added=1, removed=0)],
        findings=findings,
        verdicts={
            fid: Verdict(finding_id=fid, verdict=kind)  # type: ignore[arg-type]
            for fid, kind in (verdicts or {}).items()
        },
    )


def finding(
    id: str, line: int | None, severity: Severity = Severity.MAJOR, file: str = "cart.py"
) -> Finding:
    return Finding(
        id=id,
        file=file,
        line=line,
        severity=severity,
        title=f"Title {id}",
        rationale=f"Rationale {id}",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A branch that changed one line, so exactly one line can carry a comment."""

    def run(*args: str) -> None:
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "cart.py").write_text("one\ntwo\nthree\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    run("git", "checkout", "-q", "-b", "feature/cart")
    (tmp_path / "cart.py").write_text("one\nCHANGED\nthree\n")
    run("git", "commit", "-qam", "change")
    return tmp_path


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_API_URL"):
        monkeypatch.delenv(name, raising=False)


def sha(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def write_run(repo: Path, run: ReviewRun) -> Path:
    directory = repo / ".roboviewer" / "runs" / "latest"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return directory


class Recorder:
    """A transport that answers what it is told to and remembers what it was asked."""

    def __init__(self, *answers: tuple[int, dict]) -> None:
        self.answers = list(answers)
        self.sent: list[dict] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.url = url
        self.headers = headers
        self.sent.append(json.loads(body))
        status, payload = self.answers.pop(0)
        return status, json.dumps(payload).encode()


# ------------------------------------------------------------------ what the job already knows


def test_a_pull_request_build_names_its_own_repository_and_number() -> None:
    pull = detect(ACTIONS)

    assert pull is not None
    assert (pull.slug, pull.number, pull.name) == ("acme/app", 42, "GitHub Actions")
    assert pull.api_url == "https://api.github.com"


def test_a_branch_build_has_no_pull_request_to_post_to() -> None:
    """A push to main sets GITHUB_REPOSITORY too, and posting to a guessed
    number would put a review on somebody else's pull request."""
    assert detect({"GITHUB_REPOSITORY": "acme/app", "GITHUB_REF": "refs/heads/main"}) is None
    assert detect({}) is None


def test_an_enterprise_installation_is_read_from_the_environment() -> None:
    pull = detect({**ACTIONS, "GITHUB_API_URL": "https://git.acme.corp/api/v3"})

    assert pull is not None
    assert pull.api_url == "https://git.acme.corp/api/v3"


def test_the_token_comes_from_the_environment_and_nowhere_else() -> None:
    """Not from a login lying around on the machine: a tool that goes looking
    posts as somebody who did not ask to be posting."""
    assert token_for(pull_request.GITHUB, {"GH_TOKEN": "t2"}) == "t2"
    assert token_for(pull_request.GITHUB, {"GITHUB_TOKEN": "t1", "GH_TOKEN": "t2"}) == "t1"
    assert token_for(pull_request.GITHUB, {}) is None


def test_a_forge_with_no_publisher_says_so_rather_than_failing_later() -> None:
    """The seam takes two forges and one is written; the other answer is a
    sentence, not an AttributeError three frames down."""
    unwritten = replace(on_github("acme/app", 42), forge="bitbucket", name="Bitbucket")

    with pytest.raises(ForgeError, match="Nothing here can post to Bitbucket"):
        forge_for(unwritten, "t")


# ------------------------------------------------------------------ what gets said, and where


def test_a_finding_on_a_changed_line_becomes_a_comment_on_it() -> None:
    draft = compose(make_run([finding("F1", 2)]), {"cart.py": {2}})

    assert [(c.file, c.line) for c in draft.comments] == [("cart.py", 2)]
    assert "Title F1" in draft.comments[0].body
    assert draft.unanchored == 0


def test_a_finding_the_diff_cannot_carry_goes_into_the_body() -> None:
    """The scope gate keeps a finding a few lines either side of a changed one,
    and a forge will not anchor to a line its diff does not have. Dropping it
    would make the pull request show less than the report on disk."""
    draft = compose(make_run([finding("F1", 2), finding("F2", 40)]), {"cart.py": {2}})

    assert [c.line for c in draft.comments] == [2]
    assert draft.unanchored == 1
    assert "Title F2" in draft.body
    assert "Rationale F2" in draft.body
    assert "Title F1" not in draft.body


def test_a_finding_about_a_whole_file_has_no_line_to_sit_on() -> None:
    draft = compose(make_run([finding("F1", None)]), {"cart.py": {2}})

    assert draft.comments == []
    assert draft.unanchored == 1


def test_what_the_judge_threw_out_never_reaches_the_pull_request() -> None:
    run = make_run(
        [finding("F1", 2), finding("F2", 2), finding("F3", 2)],
        {"F1": "confirmed", "F2": "false_positive", "F3": "duplicate"},
    )
    run.out_of_scope = [finding("F9", 2)]

    draft = compose(run, {"cart.py": {2}})

    assert draft.findings == 1
    assert "Title F2" not in draft.body
    assert "Title F3" not in draft.body
    assert "Title F9" not in draft.body


def test_the_body_carries_the_judges_summary_and_a_tally() -> None:
    draft = compose(make_run([finding("F1", 2), finding("F2", 40)]), {"cart.py": {2}})

    assert "Two things worth fixing." in draft.body
    assert "**2 findings**" in draft.body
    assert "feature/cart into develop" in draft.body


def test_the_body_never_names_the_model() -> None:
    """A merge request is often public, and the name of a model can be somebody's
    corporate infrastructure — which is why the job that posts this holds it as a
    secret. A footer that printed it would publish what the job hides."""
    run = make_run([finding("F1", 2), finding("F2", 40)])

    body = compose(run, {"cart.py": {2}}).body

    assert run.model not in body
    assert f"{SIGNATURE} · feature/cart into develop" in body


def test_the_prose_for_a_refusing_forge_carries_every_finding() -> None:
    """`body` lists only what could not be anchored. A forge that takes the body
    and refuses the anchors must get the one that lists everything."""
    draft = compose(make_run([finding("F1", 2), finding("F2", 40)]), {"cart.py": {2}})

    assert "Title F1" not in draft.body
    assert "Title F1" in draft.body_alone
    assert "Title F2" in draft.body_alone


def test_a_run_that_found_nothing_still_says_so() -> None:
    draft = compose(make_run([]), {})

    assert draft.comments == []
    assert "No findings left standing." in draft.body


def test_the_worst_findings_are_decided_on_first() -> None:
    run = make_run([finding("F1", 40, Severity.NIT), finding("F2", 41, Severity.BLOCKER)])

    draft = compose(run, {})

    assert draft.body.index("Title F2") < draft.body.index("Title F1")


# ------------------------------------------------------------------ the request that goes out


def test_one_review_carries_the_body_and_every_anchored_comment() -> None:
    draft = compose(make_run([finding("F1", 2), finding("F2", 40)]), {"cart.py": {2}})
    transport = Recorder((200, {"html_url": "https://github.com/acme/app/pull/42#r1"}))

    posted = GitHubForge(token="t", transport=transport).post(
        on_github("acme/app", 42), draft
    )

    assert transport.url == "https://api.github.com/repos/acme/app/pulls/42/reviews"
    assert transport.headers["Authorization"] == "Bearer t"
    sent = transport.sent[0]
    assert sent["event"] == "COMMENT"
    assert sent["comments"] == [
        {"path": "cart.py", "line": 2, "side": "RIGHT", "body": sent["comments"][0]["body"]}
    ]
    assert posted.url.endswith("#r1")
    assert posted.comments == 1


def test_comments_the_forge_rejects_cost_the_anchors_and_not_the_review() -> None:
    """GitHub refuses the whole review over one bad line. The body names every
    finding anyway, so it goes up alone rather than the run being lost."""
    draft = compose(make_run([finding("F1", 2)]), {"cart.py": {2}})
    rejected = {
        "message": "Unprocessable",
        "errors": [{"message": "line must be part of the diff"}],
    }
    accepted = {"html_url": "https://github.com/acme/app/pull/42#r2"}
    transport = Recorder((422, rejected), (200, accepted))

    posted = GitHubForge(token="t", transport=transport).post(
        on_github("acme/app", 42), draft
    )

    assert len(transport.sent) == 2
    assert "comments" not in transport.sent[1]
    assert posted.comments == 0
    assert "line must be part of the diff" in posted.note
    # The finding that was going to be a comment has to survive into the prose;
    # `body` names only the loose ones, so posting that would have lost it.
    assert "Title F1" in transport.sent[1]["body"]
    assert "Rationale F1" in transport.sent[1]["body"]


@pytest.mark.parametrize(
    ("status", "said"),
    [
        (401, "refused the token"),
        (403, "pull-requests: write"),
        (404, "no pull request 42"),
        (500, "answered 500"),
    ],
)
def test_every_refusal_names_what_to_fix(status: int, said: str) -> None:
    draft = compose(make_run([]), {})
    forge = GitHubForge(token="t", transport=Recorder((status, {"message": "no"})))

    with pytest.raises(ForgeError) as raised:
        forge.post(on_github("acme/app", 42), draft)

    assert said in str(raised.value)


# ------------------------------------------------------------------ the command


def test_a_dry_run_prints_the_whole_review_and_sends_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = make_run([finding("F1", 2), finding("F2", 40)])
    run.base_sha, run.head_sha = sha(repo, "main"), sha(repo, "feature/cart")
    write_run(repo, run)
    for name, value in ACTIONS.items():
        monkeypatch.setenv(name, value)

    code = main(["comment", "--repo", str(repo), "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Would post to GitHub Actions: acme/app#42" in out
    assert out.count("acme/app#42") == 1  # announced once, not once per step
    assert "2 findings: 1 on the diff, 1 in the body" in out
    assert "--- cart.py:2 ---" in out
    assert "Title F2" in out


def test_the_line_a_comment_can_hang_on_comes_from_the_diff(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Line 2 is what the branch changed; line 1 is untouched code the scope
    gate let through on its margin."""
    run = make_run([finding("F1", 1)])
    run.base_sha, run.head_sha = sha(repo, "main"), sha(repo, "feature/cart")
    write_run(repo, run)
    for name, value in ACTIONS.items():
        monkeypatch.setenv(name, value)

    main(["comment", "--repo", str(repo), "--dry-run"])

    out = capsys.readouterr().out
    assert "1 finding: 0 on the diff, 1 in the body" in out
    assert "--- cart.py:1 ---" not in out


def test_without_a_token_nothing_is_sent_and_the_variable_is_named(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = make_run([finding("F1", 2)])
    run.base_sha, run.head_sha = sha(repo, "main"), sha(repo, "feature/cart")
    write_run(repo, run)
    for name, value in ACTIONS.items():
        monkeypatch.setenv(name, value)

    code = main(["comment", "--repo", str(repo)])

    assert code == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err


def test_outside_a_pipeline_the_pull_request_has_to_be_named(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = make_run([finding("F1", 2)])
    run.base_sha, run.head_sha = sha(repo, "main"), sha(repo, "feature/cart")
    write_run(repo, run)

    code = main(["comment", "--repo", str(repo), "--dry-run"])

    err = capsys.readouterr().err
    assert code == 2
    assert "--project owner/name --pull NUMBER" in err


def test_a_named_pull_request_needs_no_pipeline_at_all(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = make_run([finding("F1", 2)])
    run.base_sha, run.head_sha = sha(repo, "main"), sha(repo, "feature/cart")
    write_run(repo, run)

    code = main(
        ["comment", "--repo", str(repo), "--project", "acme/app", "--pull", "7", "--dry-run"]
    )

    assert code == 0
    assert "acme/app#7" in capsys.readouterr().out


def test_a_run_other_than_the_latest_is_posted_when_named(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--run is how a run that is no longer the latest gets posted: a rerun of
    the publishing step after the branch has moved on."""
    older = repo / ".roboviewer" / "runs" / "20260901-090000"
    older.mkdir(parents=True)
    run = make_run([finding("F1", 2)])
    run.base_sha, run.head_sha = sha(repo, "main"), sha(repo, "feature/cart")
    (older / "run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")

    code = main(
        ["comment", "--repo", str(repo), "--run", str(older),
         "--project", "acme/app", "--pull", "7", "--dry-run"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert str(older) in out
    assert "Title F1" in out


def test_a_run_this_version_cannot_read_says_so(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run written by a newer version is not a crash: the file is there and
    parses as JSON, and only the shape is wrong."""
    directory = repo / ".roboviewer" / "runs" / "latest"
    directory.mkdir(parents=True)
    (directory / "run.json").write_text('{"run_id": "x"}', encoding="utf-8")

    code = main(
        ["comment", "--repo", str(repo), "--project", "acme/app", "--pull", "7", "--dry-run"]
    )

    err = capsys.readouterr().err
    assert code == 2
    assert "not a run this version can read" in err


def test_a_clone_that_cannot_diff_the_run_says_which_commits(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The usual shape of this is a shallow CI clone: the run names two commits
    and the checkout has neither."""
    run = make_run([finding("F1", 2)])
    run.base_sha, run.head_sha = "0" * 40, "1" * 40
    write_run(repo, run)

    code = main(
        ["comment", "--repo", str(repo), "--project", "acme/app", "--pull", "7", "--dry-run"]
    )

    err = capsys.readouterr().err
    assert code == 2
    assert "Git error" in err
    assert "000000000000..111111111111" in err


def test_a_run_that_is_not_there_says_where_it_looked(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["comment", "--repo", str(repo), "--project", "acme/app", "--pull", "7", "--dry-run"]
    )

    err = capsys.readouterr().err
    assert code == 2
    assert "No run to post at" in err
    assert "roboviewer review" in err
