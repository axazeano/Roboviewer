"""The rules about the layout that no linter checks.

Public symbols on top, private ones below: ruff, pylint and flake8 all order
imports and class attributes, not module-level definitions, so it is a test. A
reader opening a module meets what it offers first and how it does it after.

And the review path stays free of `comments`. `roboviewer` reviews two git
branches; talking to GitHub is something `cli` does with a finished run. The
day that rule is only written down in docs/architecture.md is the day an import
quietly makes a review need a token.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "roboviewer"

# What a review is: the packages a run has to work through with no forge, no
# token and no network. `cli` and `benchmark` sit above them and may talk to one.
# Named without `comments`, which is the package they may not reach.
REVIEW_PATH = ("config", "provider", "repo", "reports", "review", "models.py", "observer.py")

ENFORCED = [
    # The whole tree: every module of the tool and of the instruments beside it,
    # subpackages included, or the rule stops applying the day one is added.
    *sorted(PACKAGE.rglob("*.py")),
    *sorted((ROOT / "measure").rglob("*.py")),
]


def definitions(path: Path) -> list[tuple[int, str]]:
    """Module-level defs and classes. Assignments are left out: constants often
    have to sit at the top because import-time code reads them."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        (node.lineno, node.name)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


@pytest.mark.parametrize("path", ENFORCED, ids=lambda p: str(p.relative_to(ROOT)))
def test_public_definitions_come_first(path: Path) -> None:
    defs = definitions(path)
    first_private = next((i for i, (_, name) in enumerate(defs) if name.startswith("_")), None)
    if first_private is None:
        return

    late = [f"{path.name}:{line} {name}" for line, name in defs[first_private:]
            if not name.startswith("_")]
    assert not late, f"public after private {defs[first_private][1]}: {', '.join(late)}"


def imported(path: Path) -> list[str]:
    """Every name a module imports, dotted, relative ones included."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names += [f"{module}.{alias.name}" if module else alias.name for alias in node.names]
    return names


@pytest.mark.parametrize(
    "path",
    [path for name in REVIEW_PATH for path in sorted((PACKAGE / name).rglob("*.py"))]
    + [PACKAGE / name for name in REVIEW_PATH if name.endswith(".py")],
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_the_review_path_does_not_import_the_comments(path: Path) -> None:
    reaching = [name for name in imported(path) if "comments" in name.split(".")]

    assert not reaching, f"{path.name} reaches the forge: {', '.join(reaching)}"
