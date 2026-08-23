"""Building the corpus the review baseline is measured on.

Beside the tool, not inside it. Roboviewer reads two git branches and never
talks to a forge; measuring it on public pull requests needs someone to turn a
pull request reference into a local clone first, and that someone is here. The
package sits under `measure`, outside `roboviewer` and outside the wheel, so
nothing on the review path can grow a dependency on a forge by accident.

Run it as `python -m measure.corpus measure/corpus.toml`; see docs/corpus.md.
"""
