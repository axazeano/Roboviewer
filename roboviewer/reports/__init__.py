"""What a finished run is turned into for people and pipelines.

`view` counts and filters the run once into the model every template reads;
`renders` is one module per output format — markdown and HTML through Jinja
templates in `templates/`, SARIF and GitLab Code Quality as plain serialization;
`save` writes the raw JSON and a report per requested format into the run's
directory.
"""

from __future__ import annotations

from .renders import RenderError, TemplateError
from .save import render_report, save
from .view import ReviewView, build_view

__all__ = [
    "RenderError",
    "ReviewView",
    "TemplateError",
    "build_view",
    "render_report",
    "save",
]
