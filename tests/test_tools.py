"""The tool boundary, where model output meets Python.

Tool arguments are whatever the model emitted — a line number may arrive as
`264`, `"264"` or `264.0`, and a parameter may be missing or of a type nobody
anticipated. None of that is a reason to end a review: `dispatch` turns a failed
call into text the agent can read and correct. A run of eight agents once died
because `start_line` came back as a string.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roboviewer.tools import dispatch


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository: the tools read the reviewed branch, not the disk."""
    run = lambda *args: subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "cart.py").write_text("\n".join(f"line {i}" for i in range(1, 41)) + "\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return tmp_path


def _read(repo: Path, **args: object) -> str:
    return dispatch(repo, "read_file", args, base_ref="HEAD", head_ref="HEAD", max_read_lines=800)


# ------------------------------------------------------------------ line numbers


@pytest.mark.parametrize("value", [10, "10", " 10 ", 10.0])
def test_a_line_number_is_taken_however_the_model_spelled_it(repo: Path, value: object) -> None:
    out = _read(repo, path="cart.py", start_line=value, end_line=value)
    assert "lines 10-10 of 40" in out
    assert "line 10" in out


@pytest.mark.parametrize("value", ["", "top", None, [1], -5])
def test_an_unusable_line_number_reads_the_file_from_the_start(repo: Path, value: object) -> None:
    """Not given and not understood come to the same thing: read from line 1.
    Refusing the call would cost the agent a turn to learn nothing."""
    out = _read(repo, path="cart.py", start_line=value)
    assert "lines 1-40 of 40" in out


# ------------------------------------------------------------------ failures stay local


def test_a_missing_parameter_comes_back_as_text(repo: Path) -> None:
    out = _read(repo, start_line=1)
    assert out.startswith("ERROR: required parameter missing")


def test_a_missing_file_comes_back_as_text(repo: Path) -> None:
    assert _read(repo, path="nope.py").startswith("ERROR:")


def test_an_unforeseen_argument_shape_does_not_escape_dispatch(repo: Path) -> None:
    """The catch-all. What blows up here is not ours to enumerate — the point is
    that it reaches the agent as a message instead of ending the review."""
    out = _read(repo, path={"not": "a path"}, start_line=1)
    assert out.startswith("ERROR:")


def test_an_unknown_tool_is_reported_rather_than_raised(repo: Path) -> None:
    out = dispatch(repo, "delete_everything", {}, base_ref="HEAD", head_ref="HEAD", max_read_lines=800)
    assert "unknown tool" in out
