"""What `git diff -U0` says a branch touched.

The map is load-bearing twice over: it is what the markup in the attached files
is rendered from, and it is what the scope gate measures a finding against. A
line counted in the wrong place moves both at once, and the agent would still
see a plausible-looking file.

Written against a real repository rather than a canned diff string — the point
is what git actually emits, including the cases nobody writes down: a hunk that
only deletes, a removal at the end of a file, a file that is gone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roboviewer.gitdiff import MARKER_REMOVED, annotate_file, change_map
from roboviewer.models import DiffStat


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    return tmp_path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _lines(*values: str) -> str:
    return "".join(f"{v}\n" for v in values)


def test_added_lines_are_numbered_in_the_new_file(repo: Path) -> None:
    (repo / "cart.py").write_text(_lines("one", "two", "three"))
    base = _commit(repo, "init")
    (repo / "cart.py").write_text(_lines("one", "inserted", "two", "three", "appended"))
    head = _commit(repo, "edit")

    changes = change_map(repo, base, head, ["cart.py"])

    assert changes["cart.py"].added == {2, 5}
    assert changes["cart.py"].removed_before == {}


def test_a_removed_line_is_anchored_where_it_used_to_sit(repo: Path) -> None:
    """It has no line of its own in the new file, so it hangs off the line that
    took its place — the one the markup prints it above.

    git writes `@@ -2 +1,0 @@` for a hunk that only deletes, where 1 is the last
    line that survived; the removal sat before line 2."""
    (repo / "cart.py").write_text(_lines("one", "doomed", "three"))
    base = _commit(repo, "init")
    (repo / "cart.py").write_text(_lines("one", "three"))
    head = _commit(repo, "delete")

    entry = change_map(repo, base, head, ["cart.py"])["cart.py"]

    assert entry.added == set()
    assert entry.removed_before == {2: ["doomed"]}


def test_a_replaced_line_counts_as_both(repo: Path) -> None:
    (repo / "cart.py").write_text(_lines("one", "old", "three"))
    base = _commit(repo, "init")
    (repo / "cart.py").write_text(_lines("one", "new", "three"))
    head = _commit(repo, "replace")

    entry = change_map(repo, base, head, ["cart.py"])["cart.py"]

    assert entry.added == {2}
    assert entry.removed_before == {2: ["old"]}


def test_a_removal_at_the_end_anchors_past_the_last_line(repo: Path) -> None:
    """There is no line after it, so the anchor is len + 1 — which is what
    annotate_file looks for when it flushes the tail."""
    (repo / "cart.py").write_text(_lines("one", "two", "last"))
    base = _commit(repo, "init")
    (repo / "cart.py").write_text(_lines("one", "two"))
    head = _commit(repo, "truncate")

    entry = change_map(repo, base, head, ["cart.py"])["cart.py"]

    assert entry.removed_before == {3: ["last"]}


def test_a_new_file_is_all_additions(repo: Path) -> None:
    (repo / "cart.py").write_text(_lines("one"))
    base = _commit(repo, "init")
    (repo / "extra.py").write_text(_lines("a", "b"))
    head = _commit(repo, "add a file")

    entry = change_map(repo, base, head, ["extra.py"])["extra.py"]

    assert entry.added == {1, 2}


def test_a_deleted_file_is_left_out_of_the_map(repo: Path) -> None:
    """Its `+++` header is /dev/null: there is no new version to number lines in,
    and build_annotated falls back to hunks for it."""
    (repo / "cart.py").write_text(_lines("one"))
    (repo / "gone.py").write_text(_lines("bye"))
    base = _commit(repo, "init")
    (repo / "gone.py").unlink()
    head = _commit(repo, "remove a file")

    assert change_map(repo, base, head, ["gone.py"]) == {}


def test_several_files_are_kept_apart(repo: Path) -> None:
    (repo / "a.py").write_text(_lines("one", "two"))
    (repo / "b.py").write_text(_lines("one", "two"))
    base = _commit(repo, "init")
    (repo / "a.py").write_text(_lines("one", "two", "three"))
    (repo / "b.py").write_text(_lines("changed", "two"))
    head = _commit(repo, "edit both")

    changes = change_map(repo, base, head, ["a.py", "b.py"])

    assert changes["a.py"].added == {3}
    assert changes["b.py"].added == {1}
    assert changes["b.py"].removed_before == {1: ["one"]}


def test_content_that_looks_like_diff_syntax_is_read_as_code(repo: Path) -> None:
    """A hunk body line starting with '--- ' is a deletion of a line of code, not
    a file header. Reading it as a header would drop the rest of the file."""
    (repo / "notes.md").write_text(_lines("intro", "--- a/old.py", "+++ b/old.py", "tail"))
    base = _commit(repo, "init")
    (repo / "notes.md").write_text(_lines("intro", "tail"))
    head = _commit(repo, "drop the block")

    entry = change_map(repo, base, head, ["notes.md"])["notes.md"]

    assert entry.removed_before == {2: ["--- a/old.py", "+++ b/old.py"]}


def test_no_files_means_no_git_call(repo: Path) -> None:
    assert change_map(repo, "HEAD", "HEAD", []) == {}


# ------------------------------------------------------------------ what the agent reads
#
# The anchor only matters through the markup: these are the same two deletions
# as above, rendered the way an agent receives them.


def _render(repo: Path, path: str, base: str, head: str) -> list[str]:
    entry = change_map(repo, base, head, [path])[path]
    stat = DiffStat(file=path, status="M", added=0, removed=1)
    return annotate_file(path, stat, (repo / path).read_text(), entry).splitlines()


def test_the_markup_puts_a_deleted_line_between_its_neighbours(repo: Path) -> None:
    (repo / "cart.py").write_text(_lines("one", "doomed", "three"))
    base = _commit(repo, "init")
    (repo / "cart.py").write_text(_lines("one", "three"))
    head = _commit(repo, "delete")

    body = _render(repo, "cart.py", base, head)[1:]

    assert [line.split("| ")[1] for line in body] == ["one", "doomed", "three"]
    assert MARKER_REMOVED in body[1]


def test_a_deletion_at_the_end_is_printed_after_the_last_line(repo: Path) -> None:
    """This is the tail flush in annotate_file, which had nothing to flush while
    the anchor came back one line short."""
    (repo / "cart.py").write_text(_lines("one", "two", "last"))
    base = _commit(repo, "init")
    (repo / "cart.py").write_text(_lines("one", "two"))
    head = _commit(repo, "truncate")

    body = _render(repo, "cart.py", base, head)[1:]

    assert [line.split("| ")[1] for line in body] == ["one", "two", "last"]
    assert MARKER_REMOVED in body[-1]
