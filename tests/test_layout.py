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

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "roboviewer"

ENFORCED = [
    *sorted((PACKAGE / "reports").rglob("*.py")),
    PACKAGE / "observer.py",
    PACKAGE / "cli.py",
    PACKAGE / "console.py",
    *sorted((PACKAGE / "config").rglob("*.py")),
    *sorted((PACKAGE / "provider").rglob("*.py")),
    *sorted((PACKAGE / "repo").rglob("*.py")),
    *sorted((PACKAGE / "review").rglob("*.py")),
    # Written this way from the start, so the whole package is in — every
    # module of it, subpackages included, or the rule stops applying the day
    # one is added.
    *sorted((ROOT / "corpus").rglob("*.py")),
    *sorted((ROOT / "research").rglob("*.py")),
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
