"""What `roboviewer init` asks, and what it leaves on disk.

Three things are worth pinning down. That the file it writes is the annotated
example with the answers set in it — a generated file holding only the answers
would strip the comments that are most of what a config file is for. That it
loads back through the loader, since a wizard that writes a config the tool then
refuses is worse than no wizard. And that it never overwrites without asking,
never puts a key on disk unasked, and never hangs against a stdin that cannot
answer it.
"""

from __future__ import annotations

import io
import re
import stat
import tomllib
from pathlib import Path

import pytest

from roboviewer.cli import main
from roboviewer.cli.init.questions import Cancelled, Option, Questions
from roboviewer.cli.init.wizard import STACKS, Wizard
from roboviewer.config import Config, Example, ExampleError, load_config
from roboviewer.config.example import EXAMPLES_DIR

# The interview, in order, when every answer is Enter. Named so a test that adds
# an answer says which question it is answering.
ALL_DEFAULTS = [""] * 11


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The wizard writes into ~/.config/roboviewer/, and the developer running
    these tests has one of those."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ROBOVIEWER_PROVIDER_CONFIG", raising=False)
    monkeypatch.delenv("ROBOVIEWER_API_KEY", raising=False)
    return home


def interview(answers: list[str], key: str = "") -> str:
    """Run the whole wizard on a scripted set of answers, and return what it
    printed."""
    out = io.StringIO()
    questions = Questions(
        stdin=io.StringIO("\n".join(answers) + "\n"),
        stdout=out,
        read_secret=lambda _: key,
    )
    Wizard(questions).run()
    return out.getvalue()


def written(home: Path) -> tuple[Path, Path]:
    return (
        home / ".config" / "roboviewer" / "provider.toml",
        home / ".config" / "roboviewer" / "config.toml",
    )


# ------------------------------------------------------------------ the example, edited


def test_a_live_key_is_rewritten_where_it_stands() -> None:
    example = Example.load("config")

    example.set("reviewer.model", "qwen3-coder")

    assert 'model = "qwen3-coder"' in example.text()
    assert 'model = "your-model-name"' not in example.text()


def test_the_comments_around_a_changed_key_survive() -> None:
    """The whole reason the file is edited rather than generated."""
    before = Example.load("config").text()
    example = Example.load("config")

    example.set("reviewer.model", "qwen3-coder")

    comments = [line for line in before.splitlines() if line.startswith("#")]
    kept = [line for line in example.text().splitlines() if line.startswith("#")]
    assert comments == kept


def test_a_key_the_example_only_offers_commented_out_is_brought_back() -> None:
    example = Example.load("config")

    example.set("run.output_language", "ru")

    assert 'output_language = "ru"' in example.text()
    assert '# output_language = "ru"' not in example.text()


def test_a_key_in_an_optional_section_uncomments_the_section_too() -> None:
    """[judge] ships commented out, because its absence is what means "the same
    as the reviewer". A judge model has to bring the header with it."""
    example = Example.load("config")

    example.set("judge.model", "a-stronger-model")

    text = example.text()
    assert "\n[judge]\n" in text
    assert 'model = "a-stronger-model"' in text
    assert tomllib.loads(text)["judge"] == {"model": "a-stronger-model"}


def test_a_key_named_in_prose_is_not_mistaken_for_the_setting() -> None:
    """provider.toml spells out three auth_header/auth_scheme combinations in a
    comment before setting either. The live line is the one to rewrite."""
    example = Example.load("provider")

    example.set("auth_scheme", "Token")

    assert tomllib.loads(example.text())["auth_scheme"] == "Token"
    assert '#   auth_scheme = "Token"' in example.text()


def test_a_key_the_example_does_not_carry_is_an_error() -> None:
    """The wizard and the example are two halves of one file; a key that has
    quietly left the text should stop the suite, not land without its comment."""
    with pytest.raises(ExampleError):
        Example.load("config").set("run.invented_setting", 1)


def test_stack_globs_are_added_to_the_list_already_there() -> None:
    example = Example.load("config")

    example.extend_list("run.exclude_globs", ["*.pbxproj", "**/Pods/**"], "iOS / Swift")

    globs = tomllib.loads(example.text())["run"]["exclude_globs"]
    assert globs[:2] == ["*.lock", "**/*.generated.*"]
    assert globs[-2:] == ["*.pbxproj", "**/Pods/**"]


@pytest.mark.parametrize("name", ["config", "provider"])
def test_the_shipped_example_is_a_valid_config(name: str) -> None:
    """It is what init writes, so it has to pass the same validation a
    hand-written file does."""
    raw = tomllib.loads((EXAMPLES_DIR / f"{name}.toml").read_text(encoding="utf-8"))

    Config.model_validate({"provider": raw} if name == "provider" else raw)


def test_every_stack_the_wizard_offers_is_the_block_the_example_documents() -> None:
    """Both lists exist for the same reason and are read by the same person;
    they must not drift apart."""
    documented = _documented_stacks()

    assert dict(STACKS.values()) == documented


# ------------------------------------------------------------------ the interview


def test_answering_nothing_at_all_still_writes_a_working_pair(isolated_home: Path) -> None:
    interview(ALL_DEFAULTS)
    provider, config = written(isolated_home)

    assert provider.is_file() and config.is_file()
    loaded = load_config()
    assert loaded.provider.base_url == "https://api.openai.com/v1"
    assert loaded.reviewer.model == "gpt-4o"
    assert loaded.judge is None


def test_the_answers_reach_both_files(isolated_home: Path) -> None:
    interview(
        [
            "https://gateway.example.com/v1",  # address
            "3",  # api-key: <key>
            "1",  # the key lives in a variable
            "MY_KEY",  # which variable
            "qwen3-coder",  # reviewer
            "2",  # reasoning off
            "y",  # a different judge
            "a-stronger-model",  # judge
            "1",  # judge reasoning on its default
            "ru",  # language
            "1,2",  # md and html
            "3",  # fail on major
            "1",  # iOS / Swift
        ]
    )

    cfg = load_config()
    assert cfg.provider.base_url == "https://gateway.example.com/v1"
    assert (cfg.provider.auth_header, cfg.provider.auth_scheme) == ("api-key", "")
    assert cfg.provider.api_key_env == "MY_KEY"
    assert cfg.reviewer.model == "qwen3-coder"
    assert cfg.reviewer.enable_thinking is False
    assert cfg.judge is not None and cfg.judge.model == "a-stronger-model"
    assert cfg.judge.enable_thinking is None
    assert cfg.run.output_language == "ru"
    assert cfg.run.report_formats == ["md", "html"]
    assert cfg.run.fail_on == "major"
    assert "*.pbxproj" in cfg.run.exclude_globs


def test_a_variable_that_is_not_set_is_said_so_with_the_line_that_sets_it(
    isolated_home: Path,
) -> None:
    printed = interview(ALL_DEFAULTS)

    assert "export ROBOVIEWER_API_KEY=" in printed


def test_a_key_written_into_the_file_is_the_only_way_it_reaches_the_disk(
    isolated_home: Path,
) -> None:
    printed = interview(
        [
            "",  # address
            "",  # bearer
            "2",  # the key goes into the file
            *[""] * 8,
            "n",  # do not probe the gateway
        ],
        key="sk-secret-value",
    )
    provider, _ = written(isolated_home)

    assert tomllib.loads(provider.read_text())["api_key"] == "sk-secret-value"
    assert stat.S_IMODE(provider.stat().st_mode) == 0o600
    # Not echoed while it is typed, and not printed back afterwards
    assert "sk-secret-value" not in printed


def test_the_key_stays_out_of_the_file_unless_it_was_asked_for(isolated_home: Path) -> None:
    interview(ALL_DEFAULTS)
    provider, _ = written(isolated_home)

    assert "api_key" not in tomllib.loads(provider.read_text())


def test_an_existing_file_is_left_alone_unless_the_answer_is_yes(isolated_home: Path) -> None:
    provider, config = written(isolated_home)
    config.parent.mkdir(parents=True)
    config.write_text('[reviewer]\nmodel = "already-here"\n')

    printed = interview([*ALL_DEFAULTS, "n"])  # n: do not overwrite config.toml

    assert config.read_text() == '[reviewer]\nmodel = "already-here"\n'
    assert provider.is_file()
    assert "left as it was" in printed


def test_an_interview_that_ends_early_writes_nothing(isolated_home: Path) -> None:
    """Ctrl-D halfway through is not half a config."""
    half = io.StringIO("https://gateway.example.com/v1\n")
    questions = Questions(stdin=half, stdout=io.StringIO())

    with pytest.raises(Cancelled):
        Wizard(questions).run()

    assert not any(path.exists() for path in written(isolated_home))


def test_a_stdin_that_cannot_answer_is_turned_away_rather_than_waited_on(
    isolated_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """pytest's stdin is not a terminal, which is exactly the CI case."""
    code = main(["init"])

    assert code == 2
    assert "asks questions" in capsys.readouterr().err
    assert not any(path.exists() for path in written(isolated_home))


# ------------------------------------------------------------------ one question at a time


def test_an_answer_outside_the_list_is_asked_again_rather_than_taken() -> None:
    out = io.StringIO()
    questions = Questions(stdin=io.StringIO("9\nx\n2\n"), stdout=out)

    chosen = questions.choice("Pick", [Option("a", "first"), Option("b", "second")])

    assert chosen == "b"
    assert out.getvalue().count("a number from 1 to 2") == 2


def test_several_answers_come_back_in_the_order_they_are_offered() -> None:
    questions = Questions(stdin=io.StringIO("3,1\n"), stdout=io.StringIO())

    picked = questions.several(
        "Which",
        [Option("md", "md"), Option("html", "html"), Option("sarif", "sarif")],
        default=["md"],
    )

    assert picked == ["md", "sarif"]


def test_none_of_them_is_an_answer_the_default_cannot_express() -> None:
    questions = Questions(stdin=io.StringIO("-\n"), stdout=io.StringIO())

    assert questions.several("Which", [Option("md", "md")], default=["md"]) == []


def _documented_stacks() -> dict[str, list[str]]:
    """The ready-made blocks the example lists in its comments, as a table."""
    stacks: dict[str, list[str]] = {}
    title = None
    for line in (EXAMPLES_DIR / "config.toml").read_text(encoding="utf-8").splitlines():
        body = line.lstrip("#").strip()
        if body.endswith(":") and not body.startswith('"'):
            title = body[:-1]
            stacks[title] = []
        elif title and (glob := re.fullmatch(r'"(.+)",', body)):
            stacks[title].append(glob.group(1))
        elif not body:
            title = None
    return {name: globs for name, globs in stacks.items() if globs}
