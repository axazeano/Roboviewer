"""Persisting run results: a rendered report for humans, JSON for debugging.

Nothing here knows what a report looks like. The numbers are counted once in
`view`, the layout lives in `templates`, and this module only decides what gets
written to disk. Adding a format means adding a template, not editing this file.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from .models import ReviewRun
from .templates import render
from .view import build_view

DEFAULT_TEMPLATE = "report.md.j2"
DEFAULT_TEMPLATES: tuple[str, ...] = (DEFAULT_TEMPLATE,)


def render_report(
    run: ReviewRun,
    template: str = DEFAULT_TEMPLATE,
    templates_dir: Path | None = None,
) -> str:
    """Renders a run with the named template. `templates_dir` overrides the
    bundled templates file by file, the way a custom prompt set does."""
    return render(template, build_view(run), templates_dir)


def output_name(template: str) -> str:
    """`report.md.j2` → `report.md`: the file is named after what the template
    produces, so a second format needs no second mapping to maintain."""
    return Path(template).name.removesuffix(".j2")


def save(
    run: ReviewRun,
    directory: Path,
    templates: Sequence[str] = DEFAULT_TEMPLATES,
    templates_dir: Path | None = None,
) -> list[Path]:
    """Writes a report per template plus the raw JSON. Returns the written
    reports in the order asked for; the first one is what the CLI announces and
    the TUI opens."""
    directory.mkdir(parents=True, exist_ok=True)

    reports = []
    for template in templates:
        path = directory / output_name(template)
        path.write_text(render_report(run, template, templates_dir), encoding="utf-8")
        reports.append(path)

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

    return reports
