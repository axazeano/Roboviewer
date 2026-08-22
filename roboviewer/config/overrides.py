"""Where the three overridable file sets come from.

Checklists, prompts and report templates all ship bundled and can all be
replaced from the repository under review. The rules are not identical — a
checklist is also looked up next to the working directory, because `--checklist
checklists/grouped` names one of the sets that ship with the tool — so they live
here side by side rather than pretending to be one function.

Only the directories are decided here. Loading what is in them is the business
of the subsystem that reads them: `review.checklist`, `review.prompts`,
`reports.renders`.
"""

from __future__ import annotations

from pathlib import Path

from .settings import Config

# The installed package: roboviewer/
PACKAGE_DIR = Path(__file__).resolve().parent.parent


def checklist_dir(cfg: Config, root: Path) -> Path:
    """A checklist inside the repository wins over the bundled one; a relative
    path is also tried against the working directory and the package, which is
    how `--checklist checklists/grouped` finds the sets that ship with us."""
    candidate = Path(cfg.run.checklist_dir).expanduser()
    if candidate.is_absolute():
        return candidate
    for base in (root, Path.cwd(), PACKAGE_DIR, PACKAGE_DIR.parent):
        resolved = base / candidate
        if resolved.is_dir():
            return resolved
    return root / candidate


def prompts_dir(cfg: Config, root: Path) -> Path | None:
    """None means the bundled texts."""
    return _override_dir(cfg.run.prompts_dir, root, "prompts")


def templates_dir(cfg: Config, root: Path) -> Path | None:
    """None means the bundled templates."""
    return _override_dir(cfg.run.templates_dir, root, "templates")


def _override_dir(configured: str, root: Path, name: str) -> Path | None:
    """An explicit setting wins; otherwise `.roboviewer/<name>` inside the
    repository is picked up on its own when it exists."""
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_absolute() else root / candidate
    in_repo = root / ".roboviewer" / name
    return in_repo if in_repo.is_dir() else None
