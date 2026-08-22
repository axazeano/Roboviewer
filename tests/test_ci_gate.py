"""Running inside a pipeline: which branch is reviewed, and how the job fails.

A CI job reads two things from this tool — the exit code and, when the exit code
is not zero, why. So what is pinned down here is that a finding nobody confirmed
never fails a build, that a crashed agent and a real blocker do not look alike,
and that a merge-request pipeline does not have to repeat the target branch it
already has in a variable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roboviewer.cli import ci_env, console, exit_codes, main
from roboviewer.models import Finding, ItemResult, ReviewRun, Severity, Verdict


def make_run(
    findings: list[Finding], verdicts: dict[str, str], items: list[ItemResult]
) -> ReviewRun:
    return ReviewRun(
        run_id="20260808-000000",
        repo_root="/tmp/repo",
        branch="feature/x",
        target="develop",
        base_sha="a" * 40,
        head_sha="b" * 40,
        model="test-model",
        started_at="2026-08-08T00:00:00Z",
        findings=findings,
        items=items,
        verdicts={
            fid: Verdict(finding_id=fid, verdict=kind)  # type: ignore[arg-type]
            for fid, kind in verdicts.items()
        },
    )


def finding(id: str, severity: Severity) -> Finding:
    return Finding(id=id, file="src/cart.py", line=42, severity=severity, title=id, rationale="r")


def item(status: str) -> ItemResult:
    return ItemResult(item_id="correctness", item_title="Correctness", status=status)  # type: ignore[arg-type]


# ------------------------------------------------------------------ what fails a build


def test_the_threshold_takes_everything_at_that_severity_and_worse() -> None:
    run = make_run(
        [finding("F001", Severity.BLOCKER), finding("F002", Severity.MINOR)],
        {"F001": "confirmed", "F002": "confirmed"},
        [item("ok")],
    )

    assert [f.id for f in exit_codes.blocking(run, "major")] == ["F001"]
    assert [f.id for f in exit_codes.blocking(run, "minor")] == ["F001", "F002"]
    assert exit_codes.blocking(run, exit_codes.NEVER) == []


def test_a_finding_the_judge_threw_out_does_not_fail_the_build() -> None:
    """The gate has to be trustworthy above all else, and a false positive that
    turns a pipeline red is how a team learns to pass --fail-on never."""
    run = make_run([finding("F001", Severity.BLOCKER)], {"F001": "false_positive"}, [item("ok")])

    assert exit_codes.blocking(run, "blocker") == []
    assert exit_codes.exit_code(run, "blocker") == exit_codes.OK


def test_a_finding_outside_the_changed_lines_does_not_fail_the_build() -> None:
    run = make_run([], {}, [item("ok")])
    run.out_of_scope = [finding("F009", Severity.BLOCKER)]

    assert exit_codes.exit_code(run, "blocker") == exit_codes.OK


def test_a_crashed_item_and_a_real_blocker_exit_differently() -> None:
    """One is rerun, the other is fixed in the branch — a single non-zero code
    would leave the pipeline unable to tell the reader which."""
    crashed = make_run([], {}, [item("failed")])
    found = make_run([finding("F001", Severity.BLOCKER)], {"F001": "confirmed"}, [item("ok")])

    assert exit_codes.exit_code(crashed, "blocker") == exit_codes.INCOMPLETE
    assert exit_codes.exit_code(found, "blocker") == exit_codes.FINDINGS
    assert exit_codes.exit_code(make_run([], {}, [item("ok")]), "blocker") == exit_codes.OK


def test_findings_win_over_an_incomplete_run() -> None:
    run = make_run([finding("F001", Severity.BLOCKER)], {"F001": "confirmed"}, [item("failed")])

    assert exit_codes.exit_code(run, "blocker") == exit_codes.FINDINGS


def test_without_a_gate_a_blocker_still_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """The default: reporting is the job, failing the build is opt-in — and a run
    nobody asked to gate has no line about gating in it either."""
    run = make_run([finding("F001", Severity.BLOCKER)], {"F001": "confirmed"}, [item("ok")])

    assert exit_codes.exit_code(run, exit_codes.NEVER) == exit_codes.OK
    console.gate_result(exit_codes.blocking(run, exit_codes.NEVER), exit_codes.NEVER)
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ which branch is reviewed


@pytest.mark.parametrize(
    ("variable", "forge"),
    [("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "GitLab CI"), ("GITHUB_BASE_REF", "GitHub Actions")],
)
def test_a_merge_request_pipeline_names_its_own_target(variable: str, forge: str) -> None:
    environment = ci_env.detect({variable: "develop"})

    assert environment is not None
    assert (environment.target, environment.name) == ("develop", forge)


def test_a_branch_pipeline_has_no_target_to_offer() -> None:
    """GITHUB_BASE_REF is set and empty outside a pull request, and reviewing
    against a guessed branch is worse than asking for one."""
    assert ci_env.detect({"GITHUB_BASE_REF": "", "CI_COMMIT_BRANCH": "main"}) is None
    assert ci_env.detect({}) is None


def test_the_target_branch_can_come_from_the_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(repo / "home"))
    monkeypatch.setenv("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "main")

    code = main(["--diff-only", "-C", str(repo)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Target branch from GitLab CI: main" in out
    assert "feature/x → main" in out


def test_an_explicit_target_beats_the_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(repo / "home"))
    monkeypatch.setenv("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "nowhere")

    code = main(["main", "--diff-only", "-C", str(repo)])

    assert code == 0
    assert "nowhere" not in capsys.readouterr().out


def test_a_shallow_clone_is_named_as_the_likely_cause(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default clone in both runners, and every failure it causes looks like
    something else: a branch that does not exist, a diff with no branch point."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    clone = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "-b", "feature/x", repo.as_uri(), str(clone)],
        check=True,
        capture_output=True,
    )

    code = main(["main", "--diff-only", "-C", str(clone)])

    assert code == exit_codes.SETUP
    assert "clone is shallow" in capsys.readouterr().err


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def run(*args: str) -> None:
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "cart.py").write_text("one\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    run("git", "checkout", "-qb", "feature/x")
    (tmp_path / "cart.py").write_text("one\ntwo\n")
    run("git", "commit", "-qam", "change")
    return tmp_path
