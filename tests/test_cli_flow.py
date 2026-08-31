"""What the CLI does before a request is made, which command it runs, and how
it stops.

Three things are worth pinning down. Where an overridable file set comes from —
a run that silently reads the bundled prompts instead of the ones in the
repository produces findings nobody can trace back to a text. That a step
which cannot go on exits with 2 and says why on stderr, since that is the whole
contract a script calling roboviewer has. And that every job the tool does has
a name of its own, so no flag quietly runs something other than a review.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roboviewer.cli import main
from roboviewer.cli.arguments import build_parser
from roboviewer.config import Config, overrides
from roboviewer.review.prompts import PromptError, Prompts


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run reads ~/.config/roboviewer/config.toml, and the developer running
    these tests has one. Point HOME somewhere empty."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def run(*args: str) -> None:
        subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "cart.py").write_text("one\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return tmp_path


# ------------------------------------------------------------------ where files come from


def test_a_checklist_in_the_repository_wins_over_the_bundled_one(repo: Path) -> None:
    (repo / "checklists" / "default").mkdir(parents=True)

    assert overrides.checklist_dir(Config(), repo) == repo / "checklists" / "default"


def test_a_bundled_checklist_set_is_found_by_its_relative_name(tmp_path: Path) -> None:
    cfg = Config()
    cfg.run.checklist_dir = "checklists/grouped"

    resolved = overrides.checklist_dir(cfg, tmp_path)

    assert resolved == overrides.PACKAGE_DIR / "checklists" / "grouped"
    assert resolved.is_dir()


@pytest.mark.parametrize("name", ["prompts", "templates"])
def test_an_override_directory_is_picked_up_only_when_it_exists(repo: Path, name: str) -> None:
    resolve = getattr(overrides, f"{name}_dir")
    assert resolve(Config(), repo) is None

    (repo / ".roboviewer" / name).mkdir(parents=True)
    assert resolve(Config(), repo) == repo / ".roboviewer" / name


def test_a_configured_prompts_directory_that_is_missing_is_an_error(repo: Path) -> None:
    """Falling back to the bundled texts would send out a run on prompts nobody
    chose, and the report would not say so."""
    cfg = Config()
    cfg.run.prompts_dir = "prompts/mine"

    with pytest.raises(PromptError):
        Prompts.for_run(cfg, repo)


# ------------------------------------------------------------------ how the run stops


def test_a_failing_step_exits_with_2_and_explains_itself(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["review", "--into", "main", "--repo", str(repo), "--checklist", "nowhere"])

    assert code == 2
    assert "Checklist error" in capsys.readouterr().err


def test_a_missing_repository_names_the_way_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["review", "--into", "main", "--repo", str(tmp_path)])

    assert code == 2
    assert "ROBOVIEWER_REPO" in capsys.readouterr().err


def test_a_diagnostic_command_runs_outside_a_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`list-items` has no branches to compare and no repository to read."""
    code = main(["list-items", "--repo", str(tmp_path)])

    assert code == 0
    assert "correctness" in capsys.readouterr().out


# ------------------------------------------------------------------ which command runs


def test_the_old_positional_form_is_refused_and_names_the_new_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`roboviewer develop feature/login` used to be a review. Whoever types it
    again must be told what replaced it, not only that it is invalid."""
    with pytest.raises(SystemExit) as stopped:
        main(["develop", "feature/login"])

    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "--from" in err and "--into" in err


def test_a_review_without_a_target_branch_names_the_flag_that_gives_one(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "GITHUB_BASE_REF"):
        monkeypatch.delenv(name, raising=False)

    code = main(["review", "--repo", str(repo)])

    assert code == 2
    assert "--into" in capsys.readouterr().err


def test_check_provider_asks_for_neither_a_repository_nor_a_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It probes a gateway, so it takes no --repo at all; with no key it stops
    before reaching the network."""
    monkeypatch.delenv("ROBOVIEWER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    code = main(["check-provider"])

    assert code == 2
    assert "No key found" in capsys.readouterr().out


def test_every_command_is_a_command_rather_than_a_flag() -> None:
    """Every job the tool does, each answered by its own name."""
    parser = build_parser()

    for command in ("review", "diff", "init", "list-items", "show-config", "check-provider"):
        assert parser.parse_args([command]).command == command
