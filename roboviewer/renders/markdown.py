"""The markdown report — read in a terminal, pasted into a merge request."""

from __future__ import annotations

from pathlib import Path

from ..models import ReviewRun
from ..view import build_view
from ._jinja import render_template

NAME = "md"
FILENAME = "report.md"
TEMPLATE = "report.md.j2"


def render(run: ReviewRun, templates_dir: Path | None = None) -> str:
    return render_template(TEMPLATE, build_view(run), templates_dir)
