"""The local repository an entry is reviewed in.

Two commits have to end up in a clone: the base, and the head reviewers saw.
That head is often not reachable from any branch any more — a pull request gets
force-pushed, or merged and the branch deleted — so the fetch asks for the SHAs
themselves first and falls back to `refs/pull/<n>/head`, which GitHub keeps.

Whatever arrives is pinned under `refs/benchmark/`. A commit fetched by SHA is
referenced by nothing, and the first `git gc` in that repository would collect
the very thing the benchmark exists to preserve.

No `--filter=blob:none`. A partial clone would look complete and then go to the
network for file contents in the middle of a review, which is exactly what the
benchmark is built to avoid.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

BASE_REF = "refs/benchmark/base"
HEAD_REF = "refs/benchmark/head"


class CloneError(RuntimeError):
    """A repository that could not be brought into the state the review needs."""


def prepare(
    repo_dir: Path,
    remote: str,
    base: str,
    head: str,
    fallback_refs: Sequence[str] = (),
) -> None:
    """A repository holding both commits, with the reviewed head checked out.

    The checkout is what makes `git rev-parse HEAD` answer, which the diff
    collector asks in order to tell a review of the working copy from a review
    of some other ref — and it lets a person open the clone and read the code
    reviewers were reading.
    """
    _init(repo_dir, remote)
    if not has_commits(repo_dir, base, head):
        _fetch_commits(repo_dir, remote, base, head, fallback_refs)
    _git(repo_dir, ["update-ref", BASE_REF, base])
    _git(repo_dir, ["update-ref", HEAD_REF, head])
    _git(repo_dir, ["checkout", "--quiet", "--detach", head])


def has_commits(repo_dir: Path, *shas: str) -> bool:
    """Whether every commit is already in this repository — asked of the local
    object store only, which is what lets a rerun stay off the network."""
    if not (repo_dir / ".git").is_dir():
        return False
    return all(_succeeds(repo_dir, ["cat-file", "-e", f"{sha}^{{commit}}"]) for sha in shas)


def _init(repo_dir: Path, remote: str) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").is_dir():
        _git(repo_dir, ["init", "--quiet"])
        _git(repo_dir, ["remote", "add", "origin", remote])
        return
    # The list can be edited to point an entry at a different fork
    _git(repo_dir, ["remote", "set-url", "origin", remote])


def _fetch_commits(
    repo_dir: Path,
    remote: str,
    base: str,
    head: str,
    fallback_refs: Sequence[str],
) -> None:
    attempts = [[base, head], *([f"+{ref}:refs/benchmark/fetched/{i}"] for i, ref in
                                enumerate(fallback_refs))]
    failures: list[str] = []
    for refspecs in attempts:
        failure = _try_fetch(repo_dir, refspecs)
        if failure:
            failures.append(failure)
        if has_commits(repo_dir, base, head):
            return

    missing = [sha for sha in (base, head) if not has_commits(repo_dir, sha)]
    raise CloneError(
        f"{remote} did not yield {' and '.join(missing)}. "
        f"Tried: {', '.join(' '.join(spec) for spec in attempts)}. "
        "A head that was force-pushed away after review is the usual reason; "
        "the list has to name a commit the repository still has."
        + ("\n" + "\n".join(failures) if failures else "")
    )


def _try_fetch(repo_dir: Path, refspecs: list[str]) -> str:
    """Git's own words when a fetch fails, or an empty string when it worked.

    A failure is not fatal on its own: asking for a SHA the server refuses to
    serve is the normal case that the pull ref exists to cover.
    """
    proc = _run(repo_dir, ["fetch", "--no-tags", "--quiet", "origin", *refspecs])
    if proc.returncode == 0:
        return ""
    return f"  git fetch origin {' '.join(refspecs)} → {proc.stderr.strip()}"


def _git(repo_dir: Path, args: list[str]) -> str:
    proc = _run(repo_dir, args)
    if proc.returncode != 0:
        raise CloneError(f"git {args[0]} in {repo_dir} → {proc.stderr.strip()}")
    return proc.stdout


def _succeeds(repo_dir: Path, args: list[str]) -> bool:
    return _run(repo_dir, args).returncode == 0


def _run(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, text=True)
