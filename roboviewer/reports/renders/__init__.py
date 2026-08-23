"""Report renders — one file per format.

A render answers three questions: what it is called in `--format`, what file it
writes, and how a run turns into that file's contents. So it is a module with
`NAME`, `FILENAME` and `render(run, templates_dir)`, and the directory listing
is the list of formats.

Being template-based is optional. SARIF and GitLab Code Quality are
serialization, not documents — generating JSON through a text template would
eventually produce an invalid file from a quote in the model's prose, so those
renders sit here as plain modules and never touch Jinja.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...models import ReviewRun
from ..view import build_view
from . import codequality, html, markdown, sarif
from ._errors import RenderError, TemplateError
from ._jinja import (
    DEFAULT_DIR,
    compile_template,
    environment,
    render_template,
    template_exists,
)

__all__ = [
    "DEFAULT_DIR",
    "Render",
    "RenderError",
    "TemplateError",
    "environment",
    "known",
    "prepare",
    "render_template",
    "resolve",
]


class Render(Protocol):
    NAME: str
    FILENAME: str

    def render(self, run: ReviewRun, templates_dir: Path | None = None) -> str: ...


# An explicit list rather than a directory scan: an unknown format should fail
# on a clear error, not on importing whatever module happens to match.
BUILTIN: tuple[Render, ...] = (markdown, html, sarif, codequality)
REGISTRY: dict[str, Render] = {render.NAME: render for render in BUILTIN}


def known() -> list[str]:
    return [render.NAME for render in BUILTIN]


def resolve(name: str, templates_dir: Path | None = None) -> Render:
    if name in REGISTRY:
        return REGISTRY[name]

    template = f"report.{name}.j2"
    if template_exists(template, templates_dir):
        return _CustomTemplate(NAME=name, FILENAME=f"report.{name}", TEMPLATE=template)

    raise RenderError(
        f"Unknown report format: {name}. Known: {', '.join(known())}. "
        f"A custom one is added with a {template} template in the templates directory."
    )


def prepare(formats: Sequence[str], templates_dir: Path | None = None) -> list[Render]:
    """Resolves formats and compiles their templates without rendering anything.

    Called twice: at the start of a run, so a typo in `--format` costs a second
    rather than the full token bill, and in `save()` before the first write, so
    a failure does not leave half the reports on disk.
    """
    chosen = [resolve(fmt, templates_dir) for fmt in formats]
    for render in chosen:
        template = getattr(render, "TEMPLATE", None)
        if template:
            compile_template(template, templates_dir)
    return chosen


@dataclass
class _CustomTemplate:
    """A format with no module but a template of its own in templates_dir.

    Not frozen: `Render` declares NAME and FILENAME as plain attributes, which
    a class with read-only ones does not satisfy.
    """

    NAME: str
    FILENAME: str
    TEMPLATE: str

    def render(self, run: ReviewRun, templates_dir: Path | None = None) -> str:
        return render_template(self.TEMPLATE, build_view(run), templates_dir)
