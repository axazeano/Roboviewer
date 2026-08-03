"""Persisting run results: rendered reports for humans, JSON for debugging.

Nothing here knows what a report looks like. The numbers are counted once in
`view`, each output format is a module in `renders`, and this file only decides
what gets written to disk. Adding a format means adding a render, not editing
this one.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from . import renders
from .models import ReviewRun

DEFAULT_FORMATS: tuple[str, ...] = ("md",)


def render_report(
    run: ReviewRun,
    fmt: str = DEFAULT_FORMATS[0],
    templates_dir: Path | None = None,
) -> str:
    """Renders a run in one format. `templates_dir` overrides bundled templates
    file by file, the way a custom prompt set does."""
    return renders.resolve(fmt, templates_dir).render(run, templates_dir)


def save(
    run: ReviewRun,
    directory: Path,
    formats: Sequence[str] = DEFAULT_FORMATS,
    templates_dir: Path | None = None,
) -> list[Path]:
    """Writes the raw JSON plus a report per format. Returns the written reports
    in the order asked for; the first one is what the CLI announces and the TUI
    opens."""
    # Resolved and compiled before anything is written, so a broken template
    # fails before half the reports are on disk.
    chosen = renders.prepare(formats, templates_dir)

    directory.mkdir(parents=True, exist_ok=True)

    (directory / "run.json").write_text(
        run.model_dump_json(indent=2, exclude={"items": {"__all__": {"findings"}}}),
        encoding="utf-8",
    )

    items_dir = directory / "items"
    items_dir.mkdir(exist_ok=True)
    for item in run.items:
        (items_dir / f"{item.item_id}.json").write_text(
            item.model_dump_json(indent=2), encoding="utf-8"
        )

    (directory / "findings.json").write_text(
        json.dumps(
            [
                {
                    **finding.model_dump(mode="json"),
                    "verdict": run.verdicts.get(finding.id, None)
                    and run.verdicts[finding.id].model_dump(mode="json"),
                }
                for finding in run.findings
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    latest = directory.parent / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(directory.name)
    except OSError:
        pass  # the symlink is a convenience, not a requirement

    # Last, deliberately. Rendering is the only step here that runs code someone
    # can edit, and a run costs real money: if a template blows up, the results
    # are already on disk and only the pretty part is missing.
    reports = []
    for render in chosen:
        path = directory / render.FILENAME
        path.write_text(render.render(run, templates_dir), encoding="utf-8")
        reports.append(path)

    return reports
