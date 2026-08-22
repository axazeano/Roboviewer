"""Render errors, in their own module so `_jinja` and the registry share one
hierarchy without importing each other in a circle.

One type for everything that stops a report from being produced: the caller has
a single decision to make — the run happened, but there is nothing to show.
`TemplateError` is the templated-render special case.
"""

from __future__ import annotations


class RenderError(RuntimeError):
    pass


class TemplateError(RenderError):
    pass
