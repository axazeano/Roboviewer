"""The log as one page.

Rendered from the log alone and from nothing else, which is what lets an
interrupted run still produce a readable page — and lets a page be produced
again later from a run directory somebody kept.

Self-contained like the report: styles inline, no scripts, no links out. The
folding is `<details>`, which the browser does on its own.

The Jinja environment is the tool's, templates and filters included. Rendering
HTML the same way twice is not worth two implementations, and the page is a
document beside the report rather than a document unlike it.
"""

from __future__ import annotations

from pathlib import Path

from roboviewer.reports.renders import render_template

from .records import PAGE
from .view import TraceView, load

TEMPLATE = "trace.html.j2"

# This package's own templates. The lookup falls through to the tool's bundled
# set, so the page extends the same `_layout.html.j2` the report does and there
# is one skeleton rather than two that drift.
TEMPLATES = Path(__file__).resolve().parent / "templates"


def render(view: TraceView) -> str:
    return render_template(TEMPLATE, view, TEMPLATES)


def render_into(directory: Path) -> Path | None:
    """Writes the page next to the log it renders. None when the directory
    holds no log — a run nobody watched, or the wrong directory."""
    view = load(directory)
    if view is None:
        return None
    page = directory / PAGE
    page.write_text(render(view), encoding="utf-8")
    return page
