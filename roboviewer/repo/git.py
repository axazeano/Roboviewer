"""The one way this tool talks to git.

Every subprocess call into git goes through `git()` below, so there is one
error type, one place timeouts are set, and one place to look when a command
misbehaves. Callers that need an exit code other than zero to count as an
answer — `git grep` says 1 for "no matches" — name it in `ok`.

The small helpers after it are the questions asked more than once: where the
repository is, what a ref resolves to, what a file looked like at a commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, ok: tuple[int, ...] = (0,), timeout: float | None = None) -> str:
    """Runs `git <args>` in `root` and returns stdout. Any exit code outside
    `ok` is a `GitError` carrying git's own words."""
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode not in ok:
        raise GitError(f"git {' '.join(args)} → {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def succeeds(root: Path, *args: str) -> bool:
    """Whether git answers at all — for questions whose answer is the exit code."""
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return proc.returncode == 0


def repo_root(start: Path) -> Path:
    try:
        out = git(start, "rev-parse", "--show-toplevel")
    except GitError as exc:
        raise GitError(f"{start} is not inside a git repository") from exc
    return Path(out.strip())


def current_branch(root: Path) -> str:
    name = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    return name if name != "HEAD" else git(root, "rev-parse", "--short", "HEAD").strip()


def resolve_ref(root: Path, ref: str, *, kind: str = "Branch") -> str:
    """Resolve a branch, trying the local name first and then origin/."""
    for candidate in (ref, f"origin/{ref}"):
        if succeeds(root, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"):
            return candidate
    raise GitError(f"{kind} found neither as '{ref}' nor as 'origin/{ref}'")


def merge_base(root: Path, target: str, source: str = "HEAD") -> str:
    try:
        return git(root, "merge-base", target, source).strip()
    except GitError as exc:
        # git says only "→ 1" here, and in CI the reason is nearly always the
        # same one: the branch point was never fetched.
        raise GitError(
            f"{target} and {source} have no common commit in this clone, "
            f"so there is no branch point to diff from"
        ) from exc


def is_shallow(root: Path) -> bool:
    """A clone cut off at N commits — the default in both GitLab and GitHub CI."""
    return git(root, "rev-parse", "--is-shallow-repository").strip() == "true"


def rev_parse(root: Path, ref: str = "HEAD") -> str:
    return git(root, "rev-parse", ref).strip()


def show_file(root: Path, ref: str, path: str) -> str | None:
    """File contents at the given revision, or None when there is no such file there."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True, text=True
    )
    return proc.stdout if proc.returncode == 0 else None


def looks_binary(text: str) -> bool:
    return "\x00" in text[:4000]
