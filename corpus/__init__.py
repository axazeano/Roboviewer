"""Building the corpus the review baseline is measured on.

Beside the tool, not inside it. Roboviewer reads two git branches and never
talks to a forge; measuring it on public pull requests needs someone to turn a
pull request reference into a local clone first, and that someone is here. The
package sits outside `roboviewer` and outside the wheel — `packages.find` in
pyproject.toml only includes `roboviewer*` — so nothing on the review path can
grow a dependency on a forge by accident.

Run it as `python -m corpus <list.toml>`; see docs/corpus.md.
"""
