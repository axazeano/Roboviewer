"""Checklist: a set of markdown files with a simple frontmatter block.

File format:

    ---
    id: correctness
    title: Correctness and logic errors
    enabled: true
    order: 10
    ---
    Task text for the agent...

One file is one item, and one item is one reviewing agent. An optional
`_system.md` beside them replaces the reviewer's system prompt for the set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SYSTEM_OVERRIDE = "_system.md"


@dataclass
class ChecklistItem:
    id: str
    title: str
    body: str
    enabled: bool = True
    order: int = 100
    path: Path | None = None
    # From the directory's _system.md, when present. A set that groups several
    # aspects into one agent needs it: the default system prompt says "exactly
    # one aspect, other reviewers cover the rest".
    system: str | None = None


def load_checklist(directory: Path, only: list[str] | None = None) -> list[ChecklistItem]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Checklist directory not found: {directory}")

    override = directory / SYSTEM_OVERRIDE
    system = override.read_text(encoding="utf-8").strip() if override.is_file() else None

    items: list[ChecklistItem] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue  # not an item: _system.md and anything else auxiliary
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        if not body.strip():
            continue
        item_id = meta.get("id") or path.stem
        items.append(
            ChecklistItem(
                id=item_id,
                title=meta.get("title", item_id),
                body=body,
                enabled=_as_bool(meta.get("enabled")),
                order=int(meta["order"]) if meta.get("order", "").isdigit() else 100,
                path=path,
                system=system,
            )
        )

    items = [i for i in items if i.enabled]
    if only:
        wanted = {name.strip() for name in only}
        items = [i for i in items if i.id in wanted]
        missing = wanted - {i.id for i in items}
        if missing:
            raise ValueError(f"Checklist items not found: {', '.join(sorted(missing))}")

    if not items:
        raise ValueError(f"No enabled checklist items in {directory}")

    return sorted(items, key=lambda i: (i.order, i.id))


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, raw

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip().lower()] = value.strip().strip("'\"")
    return meta, "\n".join(lines[end + 1 :]).strip()


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    # "да" stays for checklists written before the bundled set moved to English
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}
