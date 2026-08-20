"""Shared by every templated render: the Jinja environment, filters, labels.

Not a render itself, hence the underscore — same convention as the partials in
`templates/`.

The templates live in `roboviewer/templates/` and are data, not code. A file is
named `<document>.<format>.j2`, and the extension before `.j2` decides escaping:
markdown goes out as-is, HTML escapes every value, because a finding's title is
model output quoted back.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    StrictUndefined,
    Template,
    TemplateNotFound,
)
from jinja2 import TemplateError as _JinjaTemplateError
from markdown_it import MarkdownIt
from markupsafe import Markup
from pydantic import BaseModel

from ..models import SEVERITY_LABEL, Severity
from ._errors import TemplateError

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "templates" / "default"

# Presentation, so it lives next to the rendering. Both tables serve markdown
# and HTML, which is why neither is inlined in a template.
SEVERITY_ICON: dict[Severity, str] = {
    Severity.BLOCKER: "🛑",
    Severity.MAJOR: "⚠️",
    Severity.MINOR: "🔹",
    Severity.NIT: "💬",
}

STATUS_ICON: dict[str, str] = {
    "ok": "✅",
    "truncated": "⚠️",
    "failed": "❌",
    "skipped": "⏭",
    "pending": "…",
    "running": "…",
}

# `html: False` is the point: rationale and suggestion are model output and the
# report opens in a browser, so the only markup allowed through is what the
# parser produced. `breaks` matches GitLab, so the same text reads the same way
# in the report and in the merge request.
_MARKDOWN = MarkdownIt("commonmark", {"html": False, "breaks": True})


@lru_cache(maxsize=8)
def environment(directory: Path | None = None) -> Environment:
    """Cached per directory — no reason to rebuild it for every render. Jinja
    still stats template files itself, so edits are picked up between renders."""
    loaders: list[FileSystemLoader] = []
    if directory is not None:
        loaders.append(FileSystemLoader(str(directory)))
    loaders.append(FileSystemLoader(str(DEFAULT_DIR)))

    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=_autoescape,
        # Block tags sit on their own lines and must not leave blank ones behind:
        # in markdown an extra newline is a paragraph break, not whitespace.
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        # A typo in a field name has to fail loudly. Silently rendering an empty
        # string would ship a report with a section quietly missing.
        undefined=StrictUndefined,
    )
    env.filters["thousands"] = _thousands
    env.filters["percent"] = _percent
    env.filters["fixed"] = _fixed
    env.filters["size"] = _size
    env.filters["blockquote"] = _blockquote
    env.filters["markdown"] = _markdown
    env.globals["SEVERITY_LABEL"] = SEVERITY_LABEL
    env.globals["SEVERITY_ICON"] = SEVERITY_ICON
    env.globals["STATUS_ICON"] = STATUS_ICON
    return env


def template_exists(name: str, directory: Path | None = None) -> bool:
    """Whether the file exists — the file, not a compilable template. A broken
    template is "present but broken", a separate answer, and reporting it as
    absent would put a lie in the error message."""
    env = environment(directory)
    try:
        env.loader.get_source(env, name)  # type: ignore[union-attr]
    except TemplateNotFound:
        return False
    return True


def compile_template(name: str, directory: Path | None = None) -> Template:
    """Reads and compiles a template without rendering it.

    Separate from rendering because it runs at the start of a run: a typo in a
    template should cost a second, not eight agents and the full token bill.
    """
    env = environment(directory)
    try:
        return env.get_template(name)
    except TemplateNotFound as exc:
        searched = f"{directory}, " if directory is not None else ""
        raise TemplateError(
            f"Template {name} not found: looked in {searched}{DEFAULT_DIR}"
        ) from exc
    except _JinjaTemplateError as exc:
        raise TemplateError(f"Template {name} failed to parse: {exc}") from exc


def render_template(name: str, context: BaseModel, directory: Path | None = None) -> str:
    """Renders a context model with the named template. `directory` overrides
    bundled templates file by file.

    The context is any model, not the review view specifically — a document
    covering several runs has a different shape.
    """
    template = compile_template(name, directory)

    # Field by field rather than as a dump: templates keep attribute access and
    # enums stay enums, so SEVERITY_ICON[f.severity] works.
    values = {field: getattr(context, field) for field in type(context).model_fields}
    try:
        return template.render(values)
    except _JinjaTemplateError as exc:
        raise TemplateError(f"Template {name} failed to render: {exc}") from exc


def _thousands(value: int) -> str:
    """12345 → "12 345". A thin space would read better but breaks copy-paste
    out of a terminal, so it stays an ordinary one."""
    return f"{value:,}".replace(",", " ")


def _percent(value: float) -> str:
    return f"{value:.0%}"


def _fixed(value: float, digits: int = 0) -> str:
    return f"{value:.{digits}f}"


def _size(chars: int) -> str:
    """A length of text as something a reader can weigh: "812 B", "6.4 KB".

    Characters rather than bytes on disk: what is being weighed is text a model
    was handed or handed back, and outside ASCII the two are not the same.
    """
    if chars < 1024:
        return f"{chars} B"
    return f"{chars / 1024:.1f} KB"


def _blockquote(text: str, marker: str = "> ") -> str:
    return marker + text.replace("\n", "\n" + marker)


def _markdown(text: str) -> Markup:
    """Prose written as markdown, turned into HTML. Marked safe because the
    parser produced it — see `_MARKDOWN` for why that is not the same as
    trusting the text."""
    return Markup(_MARKDOWN.render(text))


def _autoescape(name: str | None) -> bool:
    """Escaping follows the target format, which is the extension before `.j2`."""
    if name is None:
        return False
    return name.removesuffix(".j2").endswith((".html", ".htm", ".xml"))
