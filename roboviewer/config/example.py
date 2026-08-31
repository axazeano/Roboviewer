"""The two annotated example files, and setting a value inside one of them.

`examples/config.toml` and `examples/provider.toml` are what somebody copies
into `~/.config/roboviewer/` by hand, and what `roboviewer init` writes for
them. Writing means editing the text rather than generating one: the comments
around a key are most of what the file is for, and a generated file that holds
only the answers leaves the reader with nothing to read the next time they open
it.

So a value is set in place. The line keeping the key is found in its section and
rewritten; a key the example only offers commented out is uncommented, together
with its section header. Nothing else in the file moves.
"""

from __future__ import annotations

import re
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"

# `[section]`, commented or not. The commented form is how the example offers a
# section that is optional — [judge] — and setting a key in one brings it back.
SECTION = re.compile(r"^#?\s*\[([^\]]+)\]\s*$")


class ExampleError(RuntimeError):
    """A key the example does not carry.

    Raised rather than appended: the wizard and the example are two halves of
    one file, and a key that has quietly disappeared from the text should stop
    the suite, not land at the bottom of somebody's config without its comment.
    """


class Example:
    """One annotated example file, with values set in place.

    Keys are dotted from the file root — `reviewer.model`, and `base_url` for
    the provider file, whose settings sit outside any section.
    """

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines()

    @classmethod
    def load(cls, name: str) -> Example:
        """`config` or `provider` — the two files that ship."""
        return cls((EXAMPLES_DIR / f"{name}.toml").read_text(encoding="utf-8"))

    def set(self, key: str, value: object) -> None:
        """Rewrite the line holding `key`, uncommenting it if that is how the
        example offers it."""
        section, _, name = key.rpartition(".")
        index = self._find(section, name)
        indent = " " * (len(self._lines[index]) - len(self._lines[index].lstrip()))
        self._lines[index] = f"{indent}{name} = {toml(value)}"
        self._uncomment_section(index, section)

    def extend_list(self, key: str, entries: list[str], title: str) -> None:
        """Add entries to a list already in the file, under a comment naming
        them. Used for `exclude_globs`, where the example's own list is the
        starting point and a stack adds to it."""
        section, _, name = key.rpartition(".")
        start = self._find(section, name)
        end = self._closing_bracket(start, key)
        addition = [f"    # {title}", *(f'    "{entry}",' for entry in entries)]
        self._lines[end:end] = addition

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"

    def _find(self, section: str, name: str) -> int:
        """The line holding the key, preferring the one that is live.

        A prose comment can open with the same key — `provider.toml` spells out
        three `auth_header =` combinations before setting it — so a commented
        line is the answer only where the example offers no live one.
        """
        commented = None
        for index in self._within(section):
            line = self._lines[index].strip()
            body = line[1:].strip() if line.startswith("#") else line
            if not re.match(rf"^{re.escape(name)}\s*=", body):
                continue
            if not line.startswith("#"):
                return index
            commented = index if commented is None else commented
        if commented is None:
            raise ExampleError(f"{section or 'the file root'} has no {name} to set")
        return commented

    def _within(self, section: str) -> list[int]:
        """Line numbers belonging to a section, its header excluded."""
        current, inside = "", []
        for index, line in enumerate(self._lines):
            header = SECTION.match(line.strip())
            if header:
                current = header.group(1)
                continue
            if current == section:
                inside.append(index)
        return inside

    def _uncomment_section(self, index: int, section: str) -> None:
        """A key set inside an optional section brings its header back."""
        if not section:
            return
        for above in range(index, -1, -1):
            header = SECTION.match(self._lines[above].strip())
            if header and header.group(1) == section:
                self._lines[above] = f"[{section}]"
                return

    def _closing_bracket(self, start: int, key: str) -> int:
        for index in range(start, len(self._lines)):
            if self._lines[index].strip() == "]":
                return index
        raise ExampleError(f"the list at {key} is never closed")


def toml(value: object) -> str:
    """A value as TOML. Only the shapes a config setting takes."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(toml(item) for item in value) + "]"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
