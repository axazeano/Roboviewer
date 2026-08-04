"""Public symbols on top, private ones below.

No linter checks this: ruff, pylint and flake8 all order imports and class
attributes, not module-level definitions. So it is a test.

The rest of the package still reads bottom-up; converting it is a separate
change, hence the explicit list rather than a package-wide sweep.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "roboviewer"

ENFORCED = sorted((PACKAGE / "renders").rglob("*.py")) + [
    PACKAGE / "cli.py",
    PACKAGE / "console.py",
    PACKAGE / "events.py",
    PACKAGE / "judge.py",
    PACKAGE / "sources.py",
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


@pytest.mark.parametrize("path", ENFORCED, ids=lambda p: p.name)
def test_public_definitions_come_first(path: Path) -> None:
    defs = definitions(path)
    first_private = next((i for i, (_, name) in enumerate(defs) if name.startswith("_")), None)
    if first_private is None:
        return

    late = [f"{path.name}:{line} {name}" for line, name in defs[first_private:]
            if not name.startswith("_")]
    assert not late, f"public after private {defs[first_private][1]}: {', '.join(late)}"
