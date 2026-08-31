"""Which file a run's settings came from.

Two files now, split so the half that gets copied around cannot carry the key:
the provider has a file of its own, and a `--config` file holding a `[provider]`
section is refused rather than honoured. The rest of the questions are the old
ones: that the default location is read when nothing names another, that
`--config` replaces it instead of stacking on top of it, and that a file sitting
in the reviewed repository is not picked up behind anyone's back. The last one
used to be a feature, and a run that quietly reads settings out of the
repository under review is exactly the surprise this is here to prevent coming
back.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roboviewer.cli import main
from roboviewer.config import (
    Config,
    home_config_path,
    load_config,
    provider_config_path,
)

HOME_CONFIG = """\
[provider]
base_url = "https://gateway.internal/v1"

[reviewer]
model = "home-model"
"""

OTHER_CONFIG = """\
[reviewer]
model = "named-model"
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer running these tests has a real config in the default
    location, and it would answer for the fixtures."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


PROVIDER_CONFIG = """\
base_url = "https://provider.internal/v1"
"""


def write_home_config(text: str = HOME_CONFIG) -> Path:
    path = home_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_provider_config(text: str = PROVIDER_CONFIG) -> Path:
    path = provider_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------- the provider is a file of its own


def test_the_provider_comes_from_its_own_file() -> None:
    provider = write_provider_config()
    write_home_config(OTHER_CONFIG)

    cfg = load_config()

    assert cfg.provider.base_url == "https://provider.internal/v1"
    assert cfg.provider_source == str(provider)
    assert cfg.reviewer.model == "named-model"


def test_a_named_file_carrying_a_provider_is_refused(tmp_path: Path) -> None:
    """The whole point of the split: the file people copy into an experiment or
    a write-up must not be able to hold a key, and saying so late — after the
    copy exists — is saying it too late."""
    write_provider_config()
    named = tmp_path / "experiment.toml"
    named.write_text(HOME_CONFIG, encoding="utf-8")

    with pytest.raises(ValueError) as exc:
        load_config(named)

    assert "[provider]" in str(exc.value)
    assert str(provider_config_path()) in str(exc.value)


def test_a_named_file_without_a_provider_runs_on_the_provider_file(
    tmp_path: Path,
) -> None:
    write_provider_config()
    named = tmp_path / "experiment.toml"
    named.write_text(OTHER_CONFIG, encoding="utf-8")

    cfg = load_config(named)

    assert cfg.reviewer.model == "named-model"
    assert cfg.provider.base_url == "https://provider.internal/v1"


def test_the_combined_file_still_works_and_says_how_to_split_it() -> None:
    """Upgrading must not break a machine that is already set up. It may say
    something, and it does — silence would leave the key where it is."""
    path = write_home_config()

    cfg = load_config()

    assert cfg.provider.base_url == "https://gateway.internal/v1"
    assert cfg.provider_source == str(path)
    assert cfg.provider_notice is not None
    assert str(provider_config_path()) in cfg.provider_notice


def test_a_runner_names_the_provider_file_through_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI has no home config, and the provider is the one thing it cannot do
    without. Without this the split would simply lock a pipeline out."""
    named = tmp_path / "ci-provider.toml"
    named.write_text('base_url = "https://runner.internal/v1"\n', encoding="utf-8")
    monkeypatch.setenv("ROBOVIEWER_PROVIDER_CONFIG", str(named))
    write_home_config(OTHER_CONFIG)

    cfg = load_config()

    assert cfg.provider.base_url == "https://runner.internal/v1"
    assert cfg.provider_source == str(named)


def test_the_provider_file_wins_over_a_section_left_behind() -> None:
    write_provider_config()
    write_home_config()

    cfg = load_config()

    assert cfg.provider.base_url == "https://provider.internal/v1"
    assert cfg.provider_notice is not None
    assert "ignored" in cfg.provider_notice


# ------------------------------------------------------------------ which file is read


def test_the_default_location_is_read_when_nothing_names_another() -> None:
    path = write_home_config()

    cfg = load_config()

    assert cfg.reviewer.model == "home-model"
    assert cfg.source == str(path)


def test_no_file_at_all_leaves_the_built_in_defaults() -> None:
    cfg = load_config()

    assert cfg.reviewer.model == Config().reviewer.model
    assert cfg.source is None


def test_a_named_file_replaces_the_default_one_rather_than_layering_over_it(
    tmp_path: Path,
) -> None:
    write_home_config()
    named = tmp_path / "other.toml"
    named.write_text(OTHER_CONFIG, encoding="utf-8")

    cfg = load_config(named)

    assert cfg.reviewer.model == "named-model"
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

    assert main(["show-config", "--repo", str(root)]) == 0

    out = capsys.readouterr().out
    assert "repo-model" not in out
    assert str(in_repo) not in out


def test_show_config_names_the_file_in_use(capsys: pytest.CaptureFixture[str]) -> None:
    path = write_home_config()

    assert main(["show-config"]) == 0

    assert str(path) in capsys.readouterr().out


def test_show_config_says_so_when_there_is_no_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["show-config"]) == 0

    # Two files now, so the line says which half is on defaults rather than
    # claiming the whole configuration is.
    out = capsys.readouterr().out
    assert "settings on defaults" in out
    assert "provider on defaults" in out


# ------------------------------------------------------------------ keys nobody reads


def test_an_unknown_key_at_the_top_level_is_refused() -> None:
    with pytest.raises(ValueError, match="nonsense"):
        Config.model_validate({"nonsense": 1})


def test_an_unknown_key_inside_provider_is_refused() -> None:
    # The one that matters: the outer model never sees inside a section, so a
    # policy set only there would let this through.
    with pytest.raises(ValueError, match="bse_url"):
        Config.model_validate({"provider": {"bse_url": "typo"}})


def test_an_unknown_key_inside_run_is_refused() -> None:
    with pytest.raises(ValueError, match="max_turnss"):
        Config.model_validate({"run": {"max_turnss": 30}})


def test_a_typo_stops_the_run_and_names_the_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_home_config('[reviewer]\nmodel = "m"\nmodle = "typo"\n')

    # 2 is "the tool could not run", the same code every other setup failure uses
    assert main(["show-config"]) == 2

    assert "modle" in capsys.readouterr().err


# ------------------------------------------------------------------ the two kinds of section


def test_a_config_in_the_old_shape_says_where_each_setting_went(tmp_path: Path) -> None:
    old = tmp_path / "old.toml"
    old.write_text(
        '[provider]\nmodel = "m"\njudge_model = "strong"\n\n[run]\nmax_turns = 30\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as caught:
        load_config(old)

    message = str(caught.value)
    # Naming the key is what forbidding extras already does; the useful half is
    # where it went, and a reader with an old file needs all three at once.
    assert "provider.model is now reviewer.model" in message
    assert "judge.model" in message
    assert "run.max_turns is now reviewer.max_turns" in message


def test_a_removed_setting_says_so_rather_than_only_being_refused(tmp_path: Path) -> None:
    old = tmp_path / "old.toml"
    old.write_text("[run]\nmin_confidence = 0.4\n", encoding="utf-8")

    with pytest.raises(ValueError, match="gone"):
        load_config(old)


def test_no_judge_section_means_the_judge_runs_as_the_reviewer() -> None:
    cfg = Config.model_validate({"reviewer": {"model": "m", "max_turns": 15}})

    assert cfg.judge is None
    assert cfg.for_judge() is cfg.reviewer
    assert cfg.for_judge().max_turns == 15


def test_the_judge_section_is_what_makes_the_two_roles_differ(
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_home_config('[reviewer]\nmodel = "m"\nmax_turns = 15\n\n[judge]\nmax_turns = 6\n')

    assert main(["show-config"]) == 0

    out = capsys.readouterr().out
    assert "max_turns    15" in out
    assert "max_turns    6" in out
