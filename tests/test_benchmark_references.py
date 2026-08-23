"""The references: what a good review of an entry finds, and the shape that
keeps them honest — every finding checked, every false claim saying why."""

from __future__ import annotations

from pathlib import Path

import pytest

from roboviewer.benchmark.items import load_items
from roboviewer.benchmark.references import load_reference

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCES = REPO_ROOT / "benchmarks" / "references"

ONE = """\
[meta]
url = "https://github.com/owner/repo/pull/42"

[[finding]]
id = "drops-a-line"
verdict = "expected"
origin = "manual"
severity = "major"
kind = "correctness"
file = "cart.py"
line = 2
what = "The second line is discarded"
evidence = "cart.py:2, the slice"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sample-42.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_reference_is_findings_with_a_verdict_each(tmp_path: Path) -> None:
    reference = load_reference(write(tmp_path, ONE))

    [finding] = reference.expected
    assert finding.id == "drops-a-line"
    assert reference.false == []


def test_a_false_claim_has_to_say_why(tmp_path: Path) -> None:
    text = ONE.replace('verdict = "expected"', 'verdict = "false"')

    with pytest.raises(ValueError, match="has to say why"):
        load_reference(write(tmp_path, text))

    assert load_reference(write(tmp_path, text + 'why_false = "it is a copy"\n')).false


def test_a_key_nobody_reads_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="extra"):
        load_reference(write(tmp_path, ONE + 'severty = "typo"\n'))


def test_a_verdict_is_one_of_two_words(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="verdict"):
        load_reference(write(tmp_path, ONE.replace('"expected"', '"maybe"')))


# ------------------------------------------------------------------ the committed references


def test_every_committed_reference_loads_and_names_an_entry() -> None:
    entries = {entry.id: entry for entry in load_items(REPO_ROOT / "benchmarks" / "items.toml")}
    files = sorted(REFERENCES.glob("*.toml"))
    assert files, "the reference the measurements were made against is committed"

    for path in files:
        reference = load_reference(path)
        entry = entries.get(path.stem)
        assert entry is not None, f"{path.name} names no entry in items.toml"
        assert reference.meta.url == entry.url
        assert reference.expected, f"{path.name} expects nothing"
        for finding in reference.finding:
            assert finding.evidence, f"{path.name}: {finding.id} carries no evidence"


def test_the_ios_reference_kept_every_finding_of_the_truth_file() -> None:
    """32 defects and 3 refuted claims came over from measure/truth.toml, and the
    two halves are told apart by verdict rather than by table name."""
    reference = load_reference(REFERENCES / "ios-4091.toml")

    assert len(reference.expected) == 32
    assert len(reference.false) == 3
    assert sum(f.origin == "manual" for f in reference.expected) == 16
