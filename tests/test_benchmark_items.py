"""What the index is allowed to say, and how `list add` and `list remove` edit it.

The index is the whole reason a benchmark can be rebuilt on another machine, so
the interesting cases are the ones where a plausible-looking file would rebuild
something other than what was measured: a branch name where a commit belongs, a
short SHA, two entries fighting over one directory.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from roboviewer.benchmark import items
from roboviewer.benchmark.items import Entry, load_items, parse_pull_url, select

REPO_ROOT = Path(__file__).resolve().parent.parent

ONE = """\
[[entry]]
id = "requests-6800"
url = "https://github.com/psf/requests/pull/6800"
base = "1111111111111111111111111111111111111111"
head = "2222222222222222222222222222222222222222"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "items.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ what an entry has to say


def test_an_entry_carries_the_four_things_a_rebuild_needs(tmp_path: Path) -> None:
    entries = load_items(write(tmp_path, ONE))

    assert len(entries) == 1
    entry = entries[0]
    assert entry.id == "requests-6800"
    assert entry.base.startswith("1111")
    assert entry.head.startswith("2222")
    assert entry.pull.slug == "psf/requests"
    assert entry.pull.number == 6800


def test_a_branch_name_where_a_commit_belongs_is_refused(tmp_path: Path) -> None:
    text = ONE.replace('head = "2222222222222222222222222222222222222222"', 'head = "main"')

    with pytest.raises(ValueError, match="40-character"):
        load_items(write(tmp_path, text))


def test_a_short_sha_is_refused(tmp_path: Path) -> None:
    text = ONE.replace('base = "1111111111111111111111111111111111111111"', 'base = "111111a"')

    with pytest.raises(ValueError, match="40-character"):
        load_items(write(tmp_path, text))


def test_a_url_that_is_not_a_pull_request_is_refused(tmp_path: Path) -> None:
    text = ONE.replace(
        'url = "https://github.com/psf/requests/pull/6800"',
        'url = "https://github.com/psf/requests"',
    )

    with pytest.raises(ValueError, match="pull request URL"):
        load_items(write(tmp_path, text))


def test_a_key_nobody_reads_is_refused(tmp_path: Path) -> None:
    # Same rule the run config follows: a key nobody reads is a field somebody
    # believed they had set
    with pytest.raises(ValueError, match="reviewrs"):
        load_items(write(tmp_path, ONE + '\nreviewrs = "typo"\n'))


def test_two_entries_cannot_share_an_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share the id"):
        load_items(write(tmp_path, ONE + ONE))


def test_an_id_that_could_climb_out_of_the_root_is_refused(tmp_path: Path) -> None:
    text = ONE.replace('id = "requests-6800"', 'id = "../elsewhere"')

    with pytest.raises(ValueError, match="directory name"):
        load_items(write(tmp_path, text))


def test_a_list_with_no_entries_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no entries"):
        load_items(write(tmp_path, "# nothing yet\n"))


def test_a_missing_file_says_so(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_items(tmp_path / "nowhere.toml")


# ------------------------------------------------------------------ the URL


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/psf/requests/pull/6800",
        "https://github.com/psf/requests/pull/6800/files",
        "https://github.com/psf/requests/pull/6800#discussion_r123",
    ],
)
def test_the_url_is_read_the_way_it_gets_copied(url: str) -> None:
    pull = parse_pull_url(url)

    assert (pull.owner, pull.repo, pull.number) == ("psf", "requests", 6800)
    assert pull.clone_url == "https://github.com/psf/requests.git"


def test_another_forge_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(ValueError, match="pull request URL"):
        parse_pull_url("https://gitlab.com/psf/requests/-/merge_requests/1")


# ------------------------------------------------------------------ narrowing the list


def test_only_narrows_the_list_in_the_order_it_was_asked_for(tmp_path: Path) -> None:
    second = ONE.replace("requests-6800", "requests-6801").replace("pull/6800", "pull/6801")
    entries = load_items(write(tmp_path, ONE + second))

    assert [e.id for e in select(entries, "requests-6801")] == ["requests-6801"]
    assert [e.id for e in select(entries, None)] == ["requests-6800", "requests-6801"]


def test_only_naming_an_entry_that_is_not_there_is_an_error(tmp_path: Path) -> None:
    entries = load_items(write(tmp_path, ONE))

    with pytest.raises(ValueError, match="no such entries: nope"):
        select(entries, "nope")


# ------------------------------------------------------------------ the committed example


def test_the_example_list_parses() -> None:
    # It is what anyone writing the real list starts from, and a template that
    # does not load is worse than none
    entries = load_items(REPO_ROOT / "benchmarks" / "items.example.toml")

    assert [entry.pull.slug for entry in entries] == ["psf/requests", "owner/repo"]


# ------------------------------------------------------------------ the committed index

# The frame from docs/benchmark-selection.md. Per-entry criteria are judgement and
# stay in that document; these are the properties of the list as a whole, and
# they are the ones an added entry quietly breaks — which is why they are a test
# rather than a paragraph.


@pytest.fixture(scope="module")
def index() -> list[Entry]:
    return load_items(REPO_ROOT / "benchmarks" / "items.toml")


def test_the_index_has_enough_entries_to_measure_anything(index: list[Entry]) -> None:
    assert len(index) >= 10


def test_every_entry_says_what_the_review_found(index: list[Entry]) -> None:
    # Without it nobody can judge whether an entry earns its place, and the
    # baseline becomes a number with no story behind it
    silent = [entry.id for entry in index if len(entry.found) < 40]
    assert not silent


def test_every_entry_records_language_domain_and_licence(index: list[Entry]) -> None:
    incomplete = [
        entry.id for entry in index if not (entry.language and entry.domain and entry.license)
    ]
    assert not incomplete


def test_the_index_is_not_one_stack(index: list[Entry]) -> None:
    assert len({entry.language for entry in index}) >= 3
    assert len({entry.domain for entry in index}) > 1


def test_no_single_repository_sets_the_tone(index: list[Entry]) -> None:
    per_repo = Counter(entry.pull.slug for entry in index)

    assert per_repo.most_common(1)[0][1] <= 3


def test_the_sizes_span_small_and_large_changes(index: list[Entry]) -> None:
    sizes = sorted(entry.added + entry.removed for entry in index)

    # A tool that does well on a one-file diff and drowns in a fifteen-file one
    # is a different tool from one that does the reverse
    assert sum(1 for size in sizes if size < 100) >= 3
    assert sum(1 for size in sizes if size > 400) >= 3


def test_every_entry_records_its_diff_size(index: list[Entry]) -> None:
    unmeasured = [
        entry.id for entry in index if entry.files == 0 or entry.added + entry.removed == 0
    ]
    assert not unmeasured


# ------------------------------------------------------------------ editing the index


def sample(**overrides: object) -> Entry:
    fields: dict[str, object] = {
        "id": "requests-6800",
        "url": "https://github.com/psf/requests/pull/6800",
        "base": "1" * 40,
        "head": "2" * 40,
        "language": "Python",
        "license": "Apache-2.0",
        "files": 3,
        "added": 74,
        "removed": 12,
    }
    fields.update(overrides)
    return Entry(**fields)  # type: ignore[arg-type]


def test_an_entry_is_rendered_as_the_table_the_loader_reads_back(tmp_path: Path) -> None:
    text = items.render(sample(domain="HTTP client", found="Retries sent an empty body."))

    [entry] = load_items(write(tmp_path, text))
    assert entry == sample(domain="HTTP client", found="Retries sent an empty body.")


def test_what_needs_judging_is_left_blank_and_hinted_at() -> None:
    text = items.render(sample())

    assert 'domain = ""    # what this repository is for' in text
    assert 'found = ""     # the defect the review found, in one line' in text


def test_quotes_in_a_field_survive_the_round_trip(tmp_path: Path) -> None:
    found = 'The "retry" path sent an empty body.'
    [entry] = load_items(write(tmp_path, items.render(sample(found=found))))

    assert entry.found == found


def test_add_creates_the_index_and_appends_to_it(tmp_path: Path) -> None:
    path = tmp_path / "benchmarks" / "items.toml"

    items.append(path, sample())
    items.append(path, sample(id="owner-42", url="https://github.com/owner/repo/pull/42"))

    assert [entry.id for entry in load_items(path)] == ["requests-6800", "owner-42"]
    assert path.read_text(encoding="utf-8").startswith("# The merge requests")


def test_add_leaves_every_other_byte_of_the_file_alone(tmp_path: Path) -> None:
    before = "# my notes\n\n" + ONE + '# why this one\nfound = "a real defect"\n'
    path = write(tmp_path, before)

    items.append(path, sample(id="owner-42", url="https://github.com/owner/repo/pull/42"))

    after = path.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after.count("[[entry]]") == 2


def test_add_refuses_a_second_entry_for_the_same_pull_request(tmp_path: Path) -> None:
    path = write(tmp_path, ONE)

    with pytest.raises(ValueError, match="already in the index"):
        items.append(path, sample(id="another-id"))
    with pytest.raises(ValueError, match="already in the index"):
        items.append(path, sample(url="https://github.com/owner/repo/pull/42"))


def test_remove_takes_one_table_out_by_id_or_url_and_keeps_the_rest(tmp_path: Path) -> None:
    second = items.render(sample(id="owner-42", url="https://github.com/owner/repo/pull/42"))
    path = write(tmp_path, "# header, kept\n\n" + ONE + "\n" + second)

    removed = items.remove(path, "requests-6800")

    assert removed.id == "requests-6800"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# header, kept\n")
    assert [entry.id for entry in load_items(path)] == ["owner-42"]

    items.remove(path, "https://github.com/owner/repo/pull/42")
    assert load_items(path, allow_empty=True) == []
    assert "# header, kept" in path.read_text(encoding="utf-8")


def test_remove_of_something_not_listed_is_an_error_not_a_no_op(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no entry with id or url"):
        items.remove(write(tmp_path, ONE), "nope")


def test_the_id_of_a_pull_request_is_repo_and_number() -> None:
    assert parse_pull_url("https://github.com/cli/cli/pull/13946").entry_id == "cli-13946"
    assert parse_pull_url("https://github.com/apache/Kafka/pull/7").entry_id == "kafka-7"
