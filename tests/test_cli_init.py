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
import os
import pty
import stat
import termios
import threading
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import pytest

from roboviewer.cli import main
from roboviewer.cli.init.questions import Cancelled, Option, Questions
from roboviewer.cli.init.wizard import Wizard
from roboviewer.config import Config, Example, ExampleError, load_config
from roboviewer.config.example import EXAMPLES_DIR

# The interview, in order, when every answer is Enter. Named so a test that adds
# an answer says which question it is answering.
ALL_DEFAULTS = [""] * 9

# What a terminal sends for each of them
UP, DOWN, SPACE, ENTER = b"\x1b[A", b"\x1b[B", b" ", b"\r"

# What the menu painted, per terminal, filled in by the thread reading it
PAINTED: dict[int, list[bytes]] = {}


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


@pytest.mark.parametrize("name", ["config", "provider"])
def test_the_shipped_example_is_a_valid_config(name: str) -> None:
    """It is what init writes, so it has to pass the same validation a
    hand-written file does."""
    raw = tomllib.loads((EXAMPLES_DIR / f"{name}.toml").read_text(encoding="utf-8"))

    Config.model_validate({"provider": raw} if name == "provider" else raw)


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
            *[""] * 5,  # model, reasoning, judge, language, reports
            "n",  # and no, do not go and probe the gateway
        ],
        key="sk-secret-value",
    )
    provider, _ = written(isolated_home)

    assert tomllib.loads(provider.read_text())["api_key"] == "sk-secret-value"
    # A key it can reach is what makes the probe worth offering; declining it is
    # what keeps this test off the network
    assert "Later, then:  roboviewer check-provider" in printed
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


# ------------------------------------------------------------------ the arrows


@dataclass
class Terminal:
    """A real terminal to answer on, since the arrows need one."""

    master: int
    reader: TextIO
    writer: TextIO

    def press(self, keys: bytes) -> None:
        os.write(self.master, keys)

    def asking(self) -> Questions:
        return Questions(stdin=self.reader, stdout=self.writer)


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> Iterator[Terminal]:
    """What the menu paints is read off continuously rather than at the end: a
    terminal nobody reads stops taking writes, and the test would hang inside a
    repaint instead of failing."""
    monkeypatch.setenv("TERM", "xterm")
    master, slave = pty.openpty()
    # Two handles rather than one: a pty is not seekable, so it cannot be opened
    # for reading and writing at once.
    reader = os.fdopen(slave, "r", buffering=1)
    writer = os.fdopen(os.dup(slave), "w", buffering=1)
    drain = threading.Thread(target=_drain, args=(master,), daemon=True)
    drain.start()

    yield Terminal(master, reader, writer)

    writer.close()
    reader.close()
    os.close(master)
    drain.join(timeout=1)


def test_the_arrows_move_the_cursor_and_enter_takes_what_is_under_it(terminal: Terminal) -> None:
    terminal.press(DOWN + ENTER)

    chosen = terminal.asking().choice("Pick", [Option("a", "first"), Option("b", "second")])

    assert chosen == "b"


def test_the_cursor_wraps_rather_than_sticking_at_the_top(terminal: Terminal) -> None:
    """Up from the first option is the last one; a cursor that stops dead reads
    as a question that is broken."""
    terminal.press(UP + ENTER)

    chosen = terminal.asking().choice(
        "Pick", [Option("a", "first"), Option("b", "second"), Option("c", "third")]
    )

    assert chosen == "c"


def test_space_marks_and_unmarks_on_a_list_that_takes_several(terminal: Terminal) -> None:
    terminal.press(DOWN + SPACE + ENTER)  # md is marked by default; add html

    picked = terminal.asking().several(
        "Reports",
        [Option("md", "md"), Option("html", "html"), Option("sarif", "sarif")],
        default=["md"],
    )

    assert picked == ["md", "html"]


def test_what_stays_on_the_screen_is_the_answer_rather_than_the_list(terminal: Terminal) -> None:
    """The list is scaffolding for one question; eleven of them left behind
    would be the whole interview twice over."""
    terminal.press(DOWN + ENTER)

    terminal.asking().choice("Pick", [Option("a", "first"), Option("b", "second")])
    terminal.writer.flush()

    assert "Pick: second" in _painted(terminal.master)


def test_escape_on_its_own_ends_the_interview(terminal: Terminal) -> None:
    """An arrow is Escape and two more characters, so a lone Escape can only be
    told apart by nothing following it."""
    terminal.press(b"\x1b")

    with pytest.raises(Cancelled):
        terminal.asking().choice("Pick", [Option("a", "first")])


def test_the_terminal_is_handed_back_as_it_was_even_when_no_answer_comes(
    terminal: Terminal,
) -> None:
    """Raw mode outlives the process if it is not restored: the person is left
    at a shell that no longer echoes what they type."""
    before = _modes(terminal.reader.fileno())
    terminal.press(b"\x1b")

    with pytest.raises(Cancelled):
        terminal.asking().choice("Pick", [Option("a", "first")])

    after = _modes(terminal.reader.fileno())
    assert after == before
    assert after[3] & termios.ECHO and after[3] & termios.ICANON


def test_without_a_terminal_the_same_question_is_numbered_and_typed() -> None:
    """A pipe, a runner, `ssh -T`, or the scripted interviews above."""
    out = io.StringIO()

    chosen = Questions(stdin=io.StringIO("2\n"), stdout=out).choice(
        "Pick", [Option("a", "first"), Option("b", "second")]
    )

    assert chosen == "b"
    assert "1) first" in out.getvalue()


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


def _modes(descriptor: int) -> list[object]:
    """The terminal's settings, without the kernel's own PENDIN bit: it says
    input happened to be waiting at the moment of the switch, which is a fact
    about this test's timing rather than about what was restored."""
    modes = termios.tcgetattr(descriptor)
    modes[3] = int(modes[3]) & ~termios.PENDIN
    return list(modes)


def _drain(master: int) -> None:
    """Everything the menu paints, kept where a test can look at it."""
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            return
        if not chunk:
            return
        PAINTED.setdefault(master, []).append(chunk)


def _painted(master: int) -> str:
    return b"".join(PAINTED.get(master, [])).decode(errors="replace")
