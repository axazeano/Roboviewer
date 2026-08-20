"""The tool boundary, where model output meets Python.

Tool arguments are whatever the model emitted — a line number may arrive as
`264`, `"264"` or `264.0`, and a parameter may be missing or of a type nobody
anticipated. None of that is a reason to end a review: `dispatch` turns a failed
call into text the agent can read and correct. A run of eight agents once died
because `start_line` came back as a string.

The answers are prompt surface too. A search that cannot say where it looked
costs turns rather than correctness: an agent told only "no matches" rewrites
the search and tries again, at a full prompt each time.
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
    out = dispatch(
        repo, "delete_everything", {}, base_ref="HEAD", head_ref="HEAD", max_read_lines=800
    )
    assert "unknown tool" in out


# --------------------------------------------------------------- searching


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A repository with files both flat in a directory and nested under it —
    the distinction a `**` glob used to fall through."""
    run = lambda *args: subprocess.run(args, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "T")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "sub").mkdir()
    (tmp_path / "pkg" / "flat.py").write_text("def discount(code):\n    return 1\n")
    (tmp_path / "pkg" / "sub" / "nested.py").write_text("total -= discount(code)\n")
    (tmp_path / "top.py").write_text("import pkg\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return tmp_path


def _grep(repo: Path, pattern: str, glob: str | None = None) -> str:
    args: dict[str, object] = {"pattern": pattern}
    if glob:
        args["glob"] = glob
    return dispatch(repo, "grep", args, base_ref="HEAD", head_ref="HEAD", max_read_lines=800)


def test_a_double_star_glob_finds_the_files_lying_flat_in_the_directory(tree: Path) -> None:
    """git's default pathspec is not a glob: `*` matches `/` too, so `pkg/**/*.py`
    demanded a directory level and walked past pkg/flat.py while reporting
    nothing. An agent has no way to see that it was told wrong."""
    out = _grep(tree, "discount", "pkg/**/*.py")

    assert "pkg/flat.py" in out
    assert "pkg/sub/nested.py" in out


def test_a_glob_that_matches_no_file_says_nothing_was_searched(tree: Path) -> None:
    out = _grep(tree, "discount", "tests/**/*.swift")

    assert "nothing was searched" in out
    assert "tests/**/*.swift" in out, "the glob is echoed back, so the agent can fix it"
    assert "No matches for" not in out, "an empty search must not read as an absent symbol"


def test_a_search_that_ran_and_found_nothing_says_how_much_it_covered(tree: Path) -> None:
    out = _grep(tree, "NoSuchSymbol", "pkg/**/*.py")

    assert out.startswith("No matches for: NoSuchSymbol")
    assert "searched 2 files" in out


def test_a_search_without_a_glob_says_it_covered_everything(tree: Path) -> None:
    assert "searched the whole tree" in _grep(tree, "NoSuchSymbol")


def test_a_hit_says_how_many_files_the_search_covered(tree: Path) -> None:
    out = _grep(tree, "discount", "pkg/**/*.py")

    assert out.splitlines()[0] == "2 matches in 2 files (searched 2 files matching `pkg/**/*.py`)"


def test_one_hit_is_counted_in_the_singular(tree: Path) -> None:
    """The answer goes to a model that reads the sentence rather than parses it."""
    out = _grep(tree, "import pkg", "*.py")

    assert out.startswith("1 match in 1 file")


def test_a_plain_extension_glob_still_matches_at_any_depth(tree: Path) -> None:
    """`:(glob)` would have made `*.py` stop at the root. On this repository that
    turned five matching files into none."""
    out = _grep(tree, "discount", "*.py")

    assert "pkg/flat.py" in out
    assert "pkg/sub/nested.py" in out


def test_a_bare_directory_glob_still_works(tree: Path) -> None:
    out = _grep(tree, "discount", "pkg")

    assert "pkg/flat.py" in out
    assert "top.py" not in out
