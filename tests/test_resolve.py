"""The reference pre-pass, against real git repositories.

It shells out to `git grep` and `git ls-tree`, so the fixtures are actual
commits rather than mocks — the failure this guards against is a pattern that
silently matches nothing, and only git can tell us whether it does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roboviewer.prompts import _references_block
from roboviewer.repo.references import MAX_SYMBOLS, ReferenceReport, check


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path):
    """A repository with a base commit; the helper adds a branch on top."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "Test")

    def write(files: dict[str, str], message: str) -> None:
        for name, body in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", message)

    write({"README.md": "base\n"}, "base")

    def branch(files: dict[str, str]) -> ReferenceReport:
        write(files, "change")
        base = subprocess.run(
            ["git", "rev-parse", "HEAD~1"], cwd=tmp_path, capture_output=True, text=True
        ).stdout.strip()
        return check(tmp_path, base, "HEAD")

    # `existing` lands before the merge base, so the branch under test does not
    # contain it — that is what makes it a check of the tree rather than the diff
    branch.existing = lambda files: write(files, "pre-existing")  # type: ignore[attr-defined]
    return branch


# ------------------------------------------------------------------ the census


def test_a_call_to_nothing_is_reported(repo) -> None:
    report = repo({"app/main.swift": "func run() {\n    helper.doesNotExistAnywhere()\n}\n"})
    assert "doesNotExistAnywhere" in report.unresolved_symbols
    assert report.unresolved_symbols["doesNotExistAnywhere"] == ["app/main.swift"]


def test_a_symbol_declared_on_the_branch_is_not_reported(repo) -> None:
    """A new type introduced by the same diff resolves — that is the difference
    between "referenced nowhere" and "does not exist"."""
    report = repo(
        {
            "app/model.swift": "struct FreshlyAdded {\n    func greet() {}\n}\n",
            "app/main.swift": "let x = FreshlyAdded()\nx.greet()\n",
        }
    )
    assert "FreshlyAdded" not in report.unresolved_symbols


def test_a_symbol_defined_outside_the_diff_is_not_reported(repo) -> None:
    """The census looks at the whole tree, not at the diff. A call into code
    this branch never touched resolves, and must stay silent."""
    repo.existing({"lib/existing.swift": "class Existing {\n    func work() {}\n}\n"})
    report = repo({"app/main.swift": "let x = Existing()\nx.work()\n"})
    assert "Existing" not in report.unresolved_symbols
    assert "work" not in report.unresolved_symbols


def test_prose_and_string_literals_do_not_become_candidates(repo) -> None:
    report = repo(
        {
            "app/main.swift": (
                "// Attempting to reconcile Anna and Alps before shipping\n"
                'let message = "Something Went Wrong Entirely"\n'
            )
        }
    )
    for word in ("Attempting", "Anna", "Alps", "Something", "Entirely"):
        assert word not in report.unresolved_symbols


def test_generated_files_are_searched_but_never_mined(repo) -> None:
    """A pbxproj is full of object ids that look exactly like identifiers.
    Mining it produced hundreds of them; it must only ever be a search target."""
    report = repo(
        {
            "App.xcodeproj/project.pbxproj": (
                "// !$*UTF8*$!\n"
                "{ B55AFB992FE2A6EE00A1A1A4 /* Thing.swift */ = {isa = PBXBuildFile;}; }\n"
            )
        }
    )
    assert "B55AFB992FE2A6EE00A1A1A4" not in report.unresolved_symbols
    assert "PBXBuildFile" not in report.unresolved_symbols


def test_the_symbol_list_is_capped_and_says_so(repo) -> None:
    calls = "\n".join(f"    obj.missingMethod{i:03d}()" for i in range(MAX_SYMBOLS + 12))
    report = repo({"app/main.swift": f"func run() {{\n{calls}\n}}\n"})
    assert len(report.unresolved_symbols) == MAX_SYMBOLS
    assert report.symbols_truncated == 12


# --------------------------------------------------------------- resource rules


def test_a_storyboard_that_does_not_exist_is_reported(repo) -> None:
    report = repo({"app/Host.swift": 'let sb = UIStoryboard(name: "Missing", bundle: nil)\n'})
    assert ("storyboard", "Missing") in [(n, v) for n, _, v, _ in report.resource_misses]


def test_a_storyboard_that_exists_is_not_reported(repo) -> None:
    report = repo(
        {
            "app/Present.storyboard": "<document/>\n",
            "app/Host.swift": 'let sb = UIStoryboard(name: "Present", bundle: nil)\n',
        }
    )
    assert not [v for n, _, v, _ in report.resource_misses if n == "storyboard"]


def test_a_storyboard_may_name_another_storyboard(repo) -> None:
    """The reference that started this: the scene lived in Main.storyboard, and
    skipping resource files as candidate sources hid it completely."""
    report = repo({"app/Main.storyboard": '<viewControllerPlaceholder storyboardName="Gone"/>\n'})
    assert ("storyboard", "Gone") in [(n, v) for n, _, v, _ in report.resource_misses]


def test_a_localization_key_with_no_entry_is_reported(repo) -> None:
    report = repo(
        {
            "en.lproj/Localizable.strings": '"_present_" = "Present";\n',
            "app/View.swift": (
                'let a = NSLocalizedString("_present_", comment: "")\n'
                'let b = NSLocalizedString("_absent_", comment: "")\n'
            ),
        }
    )
    keys = [v for n, _, v, _ in report.resource_misses if n == "localization"]
    assert keys == ["_absent_"]


def test_an_outlet_no_nib_connects_is_reported(repo) -> None:
    report = repo(
        {
            "app/View.storyboard": '<outlet property="wired" destination="x"/>\n',
            "app/View.swift": (
                "class V: UIViewController {\n"
                "    @IBOutlet weak var wired: UILabel!\n"
                "    @IBOutlet weak var dangling: NSLayoutConstraint!\n"
                "}\n"
            ),
        }
    )
    outlets = [v for n, _, v, _ in report.resource_misses if n == "outlet"]
    assert outlets == ["dangling"]


def test_a_source_file_no_manifest_mentions_is_reported(repo) -> None:
    report = repo(
        {
            "App.xcodeproj/project.pbxproj": "{ /* Registered.swift */ }\n",
            "app/Registered.swift": "let a = 1\n",
            "app/Orphan.swift": "let b = 2\n",
        }
    )
    orphans = [v for n, _, v, _ in report.resource_misses if n == "build-membership"]
    assert orphans == ["Orphan.swift"]


def test_without_a_manifest_nothing_is_claimed_about_membership(repo) -> None:
    """Most repositories have no build manifest at all. Reporting every added
    file as unregistered there would be pure noise."""
    report = repo({"app/Anything.swift": "let a = 1\n"})
    assert not [v for n, _, v, _ in report.resource_misses if n == "build-membership"]


# ------------------------------------------------------------------- rendering


def test_nothing_found_renders_no_section() -> None:
    """An empty section reads as "checked, all clean" — a different claim from
    "there was nothing to say", and a much stronger one."""
    assert _references_block(None) == ""
    assert _references_block(ReferenceReport()) == ""


def test_a_failed_pass_renders_no_section_either() -> None:
    assert _references_block(ReferenceReport(error="git exploded")) == ""


def test_repeated_questions_are_grouped_by_file() -> None:
    report = ReferenceReport(
        resource_misses=[
            ("localization", "key missing", "_a_", "View.swift"),
            ("localization", "key missing", "_b_", "View.swift"),
            ("localization", "key missing", "_c_", "Other.swift"),
        ]
    )
    block = _references_block(report)
    assert block.count("key missing") == 2  # two files, not three keys
    assert "`_a_`, `_b_`" in block
    assert "(3)" in block


def test_the_symbol_section_warns_that_it_cannot_see_dependencies() -> None:
    block = _references_block(ReferenceReport(unresolved_symbols={"AnyPublisher": ["a.swift"]}))
    assert "NOT problems" in block


def test_the_symbol_census_is_offered_as_context_not_as_findings() -> None:
    """A name that resolves nowhere is the build's to report. Inviting the agent
    to chase one costs turns for a finding it is not allowed to make."""
    block = _references_block(ReferenceReport(unresolved_symbols={"nowhere": ["a.swift"]}))
    assert "Context, not a list of findings" in block


def test_resource_misses_stay_reportable() -> None:
    """No compiler looks at a storyboard name or a localization key, so this
    half of the pre-pass is a finding rather than context."""
    block = _references_block(
        ReferenceReport(resource_misses=[("storyboard", "storyboard missing", "X", "a.swift")])
    )
    assert "No compiler looks at any of them" in block


def test_a_truncated_symbol_list_says_what_it_dropped() -> None:
    block = _references_block(
        ReferenceReport(unresolved_symbols={"a": ["x.swift"]}, symbols_truncated=7)
    )
    assert "7 more, not listed" in block
