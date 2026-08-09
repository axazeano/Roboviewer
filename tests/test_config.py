"""Which file a run's settings came from.

One file, so the useful questions are narrow: that the default location is read
when nothing names another, that `--config` replaces it instead of stacking on
top of it, and that a file sitting in the reviewed repository is not picked up
behind anyone's back. The last one used to be a feature, and a run that quietly
reads settings out of the repository under review is exactly the surprise this
is here to prevent coming back.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roboviewer.cli import main
from roboviewer.config import Config, home_config_path, load_config

HOME_CONFIG = """\
[provider]
base_url = "https://gateway.internal/v1"
model = "home-model"
"""

OTHER_CONFIG = """\
[provider]
model = "named-model"
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer running these tests has a real config in the default
    location, and it would answer for the fixtures."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def write_home_config(text: str = HOME_CONFIG) -> Path:
    path = home_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ which file is read


def test_the_default_location_is_read_when_nothing_names_another() -> None:
    path = write_home_config()

    cfg = load_config()

    assert cfg.provider.model == "home-model"
    assert cfg.source == str(path)


def test_no_file_at_all_leaves_the_built_in_defaults() -> None:
    cfg = load_config()

    assert cfg.provider.model == Config().provider.model
    assert cfg.source is None


def test_a_named_file_replaces_the_default_one_rather_than_layering_over_it(
    tmp_path: Path,
) -> None:
    write_home_config()
    named = tmp_path / "other.toml"
    named.write_text(OTHER_CONFIG, encoding="utf-8")

    cfg = load_config(named)

    assert cfg.provider.model == "named-model"
    # base_url is set in the home file and absent from the named one. Under the
    # old stacking it survived; now the named file is the whole configuration.
    assert cfg.provider.base_url == Config().provider.base_url
    assert cfg.source == str(named)


def test_a_named_file_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nowhere.toml")


def test_a_missing_default_file_is_not_an_error() -> None:
    assert not home_config_path().exists()

    assert load_config().source is None


# ------------------------------------------------------------------ what --show-config says


def repository(root: Path) -> Path:
    def run(*args: str) -> None:
        subprocess.run(args, cwd=root, check=True, capture_output=True)

    root.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (root / "cart.py").write_text("one\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return root


def test_a_config_inside_the_reviewed_repository_is_not_picked_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repository(tmp_path / "repo")
    in_repo = root / ".roboviewer" / "config.toml"
    in_repo.parent.mkdir(parents=True)
    in_repo.write_text('[provider]\nmodel = "repo-model"\n', encoding="utf-8")

    assert main(["--show-config", "-C", str(root)]) == 0

    out = capsys.readouterr().out
    assert "repo-model" not in out
    assert str(in_repo) not in out


def test_show_config_names_the_file_in_use(capsys: pytest.CaptureFixture[str]) -> None:
    path = write_home_config()

    assert main(["--show-config"]) == 0

    assert str(path) in capsys.readouterr().out


def test_show_config_says_so_when_there_is_no_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--show-config"]) == 0

    assert "everything on defaults" in capsys.readouterr().out


# ------------------------------------------------------------------ keys nobody reads


def test_an_unknown_key_at_the_top_level_is_refused() -> None:
    with pytest.raises(ValueError, match="nonsense"):
        Config.model_validate({"nonsense": 1})


def test_an_unknown_key_inside_provider_is_refused() -> None:
    # The one that matters: the outer model never sees inside a section, so a
    # policy set only there would let this through.
    with pytest.raises(ValueError, match="jugde_model"):
        Config.model_validate({"provider": {"jugde_model": "typo"}})


def test_an_unknown_key_inside_run_is_refused() -> None:
    with pytest.raises(ValueError, match="max_turnss"):
        Config.model_validate({"run": {"max_turnss": 30}})


def test_a_typo_stops_the_run_and_names_the_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_home_config('[provider]\nmodel = "m"\njugde_model = "typo"\n')

    # 2 is "the tool could not run", the same code every other setup failure uses
    assert main(["--show-config"]) == 2

    assert "jugde_model" in capsys.readouterr().err
