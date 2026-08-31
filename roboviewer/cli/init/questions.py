"""Asking one question and getting one answer back.

Every question has a default, so the whole interview can be walked through with
Enter and still produce a working setup. A bad answer is asked again rather than
taken: this writes the file somebody's runs will be made of, and "unrecognised,
so never mind" is how a typo becomes a config nobody chose.

A list of answers is offered two ways, and they are the same question drawn
differently. At a terminal the arrows move a cursor through it and Enter takes
what is under it; anywhere else — a pipe, `ssh -T`, a scripted interview, a
`TERM` that cannot move a cursor — the options are numbered and the number is
typed. The typed form is not a leftover: it is the only one that works without a
terminal, and it is what the suite drives.

The streams are arguments rather than `input()` for the same reason.
"""

from __future__ import annotations

import getpass
import os
import select
import shutil
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TextIO

# Windows has neither, and that is the whole of what it costs there: the typed
# form covers it, as it covers every stream that is not a terminal.
try:
    import termios
    import tty

    ARROWS_POSSIBLE = True
except ImportError:  # pragma: no cover - not POSIX
    ARROWS_POSSIBLE = False


class Cancelled(RuntimeError):
    """The interview ended before it was finished — Ctrl-D, Escape, or a closed
    stream."""


@dataclass(frozen=True)
class Option:
    """One answer on a list. `note` is the half-line that says what it costs."""

    value: str
    label: str
    note: str = ""


class Questions:
    """The interview, over one pair of streams."""

    def __init__(
        self,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        read_secret: Callable[[str], str] | None = None,
    ) -> None:
        self._in = stdin if stdin is not None else sys.stdin
        self._out = stdout if stdout is not None else sys.stdout
        # getpass reads from the terminal directly, so a scripted interview
        # passes something that reads from its own stream instead.
        self._read_secret = read_secret

    def say(self, text: str = "") -> None:
        print(text, file=self._out)

    def heading(self, text: str) -> None:
        print(f"\n{text}", file=self._out)

    def text(self, prompt: str, default: str = "", *, required: bool = False) -> str:
        """A line of text. `required` keeps asking until there is one."""
        while True:
            answer = self._read(f"  {prompt}{_shown(default)}: ")
            if answer:
                return answer
            if default or not required:
                return default
            self.say("  Needed to go on.")

    def yes_no(self, prompt: str, default: bool) -> bool:
        shown = "Y/n" if default else "y/N"
        while True:
            answer = self._read(f"  {prompt} [{shown}]: ").lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.say("  y or n.")

    def choice(self, prompt: str, options: list[Option], default: int = 0) -> str:
        """One of a list; returns the chosen option's value."""
        if self._at_a_terminal():
            return _Menu(self._in, self._out, prompt, options).one(default)
        return self._typed_choice(prompt, options, default)

    def several(self, prompt: str, options: list[Option], default: list[str]) -> list[str]:
        """Any number of a list, in the order the options are offered — so the
        same set of answers writes the same file however it was picked."""
        if self._at_a_terminal():
            return _Menu(self._in, self._out, prompt, options).some(default)
        return self._typed_several(prompt, options, default)

    def key(self, prompt: str) -> str:
        """A secret: read without an echo, and never printed back."""
        while True:
            answer = self._ask_secret(f"  {prompt}: ").strip()
            if answer:
                return answer
            self.say("  Needed to go on.")

    def _at_a_terminal(self) -> bool:
        """Both halves, not just the input: `roboviewer init > log` still has a
        keyboard, and drawing a moving cursor into that file helps nobody."""
        if not ARROWS_POSSIBLE or os.environ.get("TERM", "") in ("", "dumb"):
            return False
        return _is_terminal(self._in) and _is_terminal(self._out)

    def _typed_choice(self, prompt: str, options: list[Option], default: int) -> str:
        self.say(f"  {prompt}")
        self._list(options)
        while True:
            answer = self._read(f"  > [{default + 1}]: ")
            if not answer:
                return options[default].value
            picked = _number(answer, len(options))
            if picked is not None:
                return options[picked].value
            self.say(f"  a number from 1 to {len(options)}.")

    def _typed_several(self, prompt: str, options: list[Option], default: list[str]) -> list[str]:
        """Comma-separated. Empty takes the default, and `-` takes none."""
        self.say(f"  {prompt}")
        self._list(options)
        shown = ", ".join(str(i + 1) for i, o in enumerate(options) if o.value in default) or "-"
        while True:
            answer = self._read(f"  > [{shown}]: ")
            if not answer:
                return default
            if answer == "-":
                return []
            picked = [_number(part, len(options)) for part in answer.split(",")]
            if all(index is not None for index in picked):
                chosen = {index for index in picked if index is not None}
                return [o.value for i, o in enumerate(options) if i in chosen]
            self.say(f"  numbers from 1 to {len(options)}, comma-separated, or - for none.")

    def _read(self, prompt: str) -> str:
        self._out.write(prompt)
        self._out.flush()
        line = self._in.readline()
        if not line:
            raise Cancelled("the interview ended")
        return line.strip()

    def _ask_secret(self, prompt: str) -> str:
        if self._read_secret is not None:
            return self._read_secret(prompt)
        return getpass.getpass(prompt, stream=self._out)

    def _list(self, options: list[Option]) -> None:
        """Notes line up in a column; a label with no note keeps no padding."""
        for number, option in enumerate(options, start=1):
            self.say(f"    {number}) {_labelled(option, _width(options))}")


class _Menu:
    """One question drawn in place: the arrows move a cursor, the list repaints
    where it stands, and the answer replaces it with a single line.

    Repainting means counting the lines just written and stepping back over
    them, so every line is truncated to the width of the terminal — a line that
    wraps is two lines to the terminal and one to the count, and from there the
    list walks down the screen.
    """

    UP = "up"
    DOWN = "down"
    ENTER = "enter"
    SPACE = "space"
    CANCEL = "cancel"

    def __init__(self, stdin: TextIO, stdout: TextIO, prompt: str, options: list[Option]) -> None:
        self._in, self._out = stdin, stdout
        self._prompt, self._options = prompt, options
        self._painted = 0

    def one(self, default: int) -> str:
        at = self._walk(default, marks=None, hint="↑↓ move · Enter accepts")
        chosen = self._options[at]
        self._settle(chosen.label)
        return chosen.value

    def some(self, default: list[str]) -> list[str]:
        marks = {index for index, o in enumerate(self._options) if o.value in default}
        self._walk(0, marks=marks, hint="↑↓ move · space marks · Enter accepts")
        picked = [o for index, o in enumerate(self._options) if index in marks]
        self._settle(", ".join(o.label for o in picked) or "none")
        return [o.value for o in picked]

    def _walk(self, at: int, marks: set[int] | None, hint: str) -> int:
        """The loop both questions share: paint, read one key, act on it."""
        with _raw_mode(self._in), _cursor_hidden(self._out):
            while True:
                self._paint(at, marks, hint)
                key = _read_key(self._in)
                if key == self.ENTER:
                    return at
                if key == self.CANCEL:
                    raise Cancelled("the interview ended")
                if key == self.SPACE and marks is not None:
                    marks ^= {at}
                at = self._moved(key, at)

    def _moved(self, key: str, at: int) -> int:
        """Wraps around: five options and a cursor stuck at the bottom is a
        question that looks broken."""
        if key == self.UP:
            return (at - 1) % len(self._options)
        if key == self.DOWN:
            return (at + 1) % len(self._options)
        return at

    def _paint(self, at: int, marks: set[int] | None, hint: str) -> None:
        width = _width(self._options)
        lines = [f"  {self._prompt}   {hint}"]
        for index, option in enumerate(self._options):
            cursor = "▸" if index == at else " "
            mark = "" if marks is None else ("✓ " if index in marks else "· ")
            lines.append(f"    {cursor} {mark}{_labelled(option, width)}")
        self._repaint(lines)

    def _settle(self, answer: str) -> None:
        """The list is scaffolding; what stays in the scrollback is the answer."""
        self._repaint([f"  {self._prompt}: {answer}"])
        self._painted = 0

    def _repaint(self, lines: list[str]) -> None:
        if self._painted:
            # Back to the start of the block, then clear everything below it
            self._out.write(f"\x1b[{self._painted}F\x1b[0J")
        columns = shutil.get_terminal_size().columns
        self._out.write("".join(f"{_fitted(line, columns)}\n" for line in lines))
        self._out.flush()
        self._painted = len(lines)


@contextmanager
def _raw_mode(stream: TextIO) -> Iterator[None]:
    """Keys as they are pressed, rather than a line at a time.

    Restored whatever happens: an exception on the way out of here leaves
    somebody's terminal without an echo, which outlives the process. cbreak
    keeps signals on, so Ctrl-C is still Ctrl-C.
    """
    descriptor = stream.fileno()
    saved = termios.tcgetattr(descriptor)
    try:
        # TCSADRAIN rather than the TCSAFLUSH setcbreak defaults to: flushing
        # throws away whatever was typed while the question was being drawn,
        # and a key pressed a moment early is still a key that was pressed.
        tty.setcbreak(descriptor, termios.TCSADRAIN)
        yield
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


@contextmanager
def _cursor_hidden(stream: TextIO) -> Iterator[None]:
    stream.write("\x1b[?25l")
    stream.flush()
    try:
        yield
    finally:
        stream.write("\x1b[?25h")
        stream.flush()


# What a keypress means. Ctrl-C is here for the terminal that has signals off;
# with cbreak it never arrives as a character in the first place.
_KEYS = {
    "\r": _Menu.ENTER,
    "\n": _Menu.ENTER,
    " ": _Menu.SPACE,
    "\x04": _Menu.CANCEL,
    "\x03": _Menu.CANCEL,
    "k": _Menu.UP,
    "j": _Menu.DOWN,
}


def _read_key(stream: TextIO) -> str:
    """One keypress, named. Anything unrecognised comes back empty, and the
    caller stays where it was."""
    pressed = _typed(stream, 1)
    if not pressed:
        return _Menu.CANCEL
    if pressed == "\x1b":
        return _escape(stream)
    return _KEYS.get(pressed.lower(), "")


def _escape(stream: TextIO) -> str:
    """An arrow arrives as Escape and two more characters. Escape alone is a
    person leaving, so nothing following it within a moment means exactly
    that — reading on regardless would hang until the next keypress."""
    if not select.select([stream.fileno()], [], [], 0.05)[0]:
        return _Menu.CANCEL
    return {"[A": _Menu.UP, "[B": _Menu.DOWN}.get(_typed(stream, 2), "")


def _typed(stream: TextIO, count: int) -> str:
    """Straight off the descriptor, not through the stream's own buffer.

    `select` above asks the descriptor what has arrived, and a read that
    buffers ahead would leave those two answers describing different things:
    the arrow already sitting in Python's buffer would look, to select, like
    nothing following the Escape at all.
    """
    return os.read(stream.fileno(), count).decode(errors="replace")


def _fitted(line: str, columns: int) -> str:
    """Cut to the width of the terminal, since a line that wraps is two lines to
    the terminal and one to the repaint's count — and from there the list walks
    down the screen."""
    if len(line) < columns:
        return line
    return line[: columns - 2] + "…"


def _is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, ValueError, OSError):
        # A StringIO has no descriptor to ask about, and a closed stream raises
        return False


def _labelled(option: Option, width: int) -> str:
    return f"{option.label:<{width}}   {option.note}" if option.note else option.label


def _width(options: list[Option]) -> int:
    return max(len(option.label) for option in options)


def _shown(default: str) -> str:
    return f" [{default}]" if default else ""


def _number(answer: str, count: int) -> int | None:
    """A 1-based answer as a 0-based index, or None if it is not one."""
    text = answer.strip()
    if not text.isdigit() or not 1 <= int(text) <= count:
        return None
    return int(text) - 1
