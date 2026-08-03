"""The HTML report — one self-contained file.

Styles inline, no links, no scripts, no external images: this report is opened
by double click and attached to a ticket, so it has nowhere to fetch from.
"""

from __future__ import annotations

from pathlib import Path

from ..models import ReviewRun
from ..view import build_view
from ._jinja import render_template

NAME = "html"
FILENAME = "report.html"
TEMPLATE = "report.html.j2"


def render(run: ReviewRun, templates_dir: Path | None = None) -> str:
    return render_template(TEMPLATE, build_view(run), templates_dir)
