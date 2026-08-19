"""Roboviewer — a local automated merge request reviewer."""

from importlib.metadata import version

# Read from the installed metadata rather than repeated here: pyproject.toml is
# the one place the number lives, and the release tag is checked against it.
__version__ = version("roboviewer")
