"""Instruments for asking what a run did, not what it concluded.

The report says which findings came out. This package watches the run that
produced them: which prompt each agent was given, what it said between turns,
which files it opened and over what lines, what it searched for and how much
came back. That is a question about the model and the context we hand it, and
it is asked while tuning rather than while reviewing.

So it lives beside the tool rather than inside it, the way `corpus` does: not
part of the wheel, run from the repository root, and off the review path
entirely. The tool keeps nothing and renders nothing — it reports through
`roboviewer.observe`, with nobody listening unless a command here is what
started the run.

```bash
python -m research review develop          # a recorded run, then the page
python -m research page .roboviewer/runs/latest
```

Four parts, in the order the data moves: `recorder` writes the log as the run
happens, `records` is the vocabulary it writes in, `view` reads it back as
questions a person asks, `render` turns that into one HTML page.
"""

from __future__ import annotations

from .recorder import AgentRecorder, Recorder
from .records import LOG, PAGE
from .render import render, render_into
from .view import TraceView, build, load

__all__ = [
    "LOG",
    "PAGE",
    "AgentRecorder",
    "Recorder",
    "TraceView",
    "build",
    "load",
    "render",
    "render_into",
]
