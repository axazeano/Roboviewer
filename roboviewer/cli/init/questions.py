"""Asking one question and getting one answer back.

Every question has a default, so the whole interview can be walked through with
Enter and still produce a working setup. A bad answer is asked again rather than
taken: this writes the file somebody's runs will be made of, and "unrecognised,
so never mind" is how a typo becomes a config nobody chose.

The streams are arguments rather than `input()` so the suite can script an
interview, which is the only way to test the wizard at all.
"""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO


class Cancelled(RuntimeError):
    """The interview ended before it was finished — Ctrl-D, or a closed stream."""


@dataclass(frozen=True)
class Option:
    """One numbered answer. `note` is the half-line that says what it costs."""

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
        """One of a numbered list; returns the chosen option's value."""
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

    def several(self, prompt: str, options: list[Option], default: list[str]) -> list[str]:
        """Any number of the list, comma-separated. Empty takes the default,
        and `-` takes none of them."""
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
                # Listed in the options' own order, so 3,1 and 1,3 write the same file
                chosen = {index for index in picked if index is not None}
                return [o.value for i, o in enumerate(options) if i in chosen]
            self.say(f"  numbers from 1 to {len(options)}, comma-separated, or - for none.")

    def key(self, prompt: str) -> str:
        """A secret: read without an echo, and never printed back."""
        while True:
            answer = self._ask_secret(f"  {prompt}: ").strip()
            if answer:
                return answer
            self.say("  Needed to go on.")

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
        width = max(len(option.label) for option in options)
        for number, option in enumerate(options, start=1):
            label = f"{option.label:<{width}}   {option.note}" if option.note else option.label
            self.say(f"    {number}) {label}")


def _shown(default: str) -> str:
    return f" [{default}]" if default else ""


def _number(answer: str, count: int) -> int | None:
    """A 1-based answer as a 0-based index, or None if it is not one."""
    text = answer.strip()
    if not text.isdigit() or not 1 <= int(text) <= count:
        return None
    return int(text) - 1
