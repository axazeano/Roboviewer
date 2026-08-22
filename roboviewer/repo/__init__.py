"""Reading the repository under review.

Everything that touches git is here and nowhere else. `git` is the one wrapper;
`diff` reads what a branch changed; `annotate` renders changed files in full
with the changed lines marked up; `references` searches the tree for what the
diff introduces that resolves to nothing; `tools` are the read-only tools an
agent drives the repository with; and `changeset` puts the first four together
into the `ChangeSet` the rest of the review reads.

Nothing here knows about findings, prompts or models: this package answers
questions about code, and the review decides what to ask.
"""

from __future__ import annotations

from .changeset import Attachments, ChangeSet, Comparison, ContextBudget, collect
from .git import GitError, is_shallow, repo_root

__all__ = [
    "Attachments",
    "ChangeSet",
    "Comparison",
    "ContextBudget",
    "GitError",
    "collect",
    "is_shallow",
    "repo_root",
]
