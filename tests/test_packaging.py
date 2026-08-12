"""Every bundled file the tool reads at runtime is declared as package data.

Checklists, prompts and templates are data, not code, so setuptools ships them
only because `tool.setuptools.package-data` lists a pattern that matches them.
An editable install reads the source tree either way, which is how a missing
pattern stays invisible until someone installs the wheel — a new checklist set,
or a template with an extension nobody thought to declare, and the first release
goes out without it.

The patterns are expanded here exactly as setuptools expands them, so this
compares what a wheel would carry against what is on disk without building one.
"""

from __future__ import annotations

import tomllib
from glob import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "roboviewer"


def declared() -> set[str]:
    """Package-relative paths the package-data patterns match."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["roboviewer"]
    return {
        path
        for pattern in patterns
        for path in glob(pattern, root_dir=PACKAGE, recursive=True)
    }


def bundled() -> set[str]:
    """Everything under the package that is not Python and not a build artefact."""
    return {
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*")
        if path.is_file()
        and path.suffix != ".py"
        and "__pycache__" not in path.parts
    }


def test_package_data_covers_every_bundled_file() -> None:
    missing = sorted(bundled() - declared())
    assert not missing, (
        "not shipped by any package-data pattern in pyproject.toml: " + ", ".join(missing)
    )
