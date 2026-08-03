"""The report is pinned to golden files.

Rendering moved from hand-assembled lists to Jinja templates; the point of these
tests is that the move changed nothing a reader would see. The goldens were
generated from the pre-Jinja renderer, so a diff here means the template drifted
from the markdown people are used to reading.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from roboviewer import renders
from roboviewer.models import (
    DiffStat,
    Finding,
    ItemResult,
    ReviewRun,
    Severity,
    Usage,
    Verdict,
)
from roboviewer.renders import _jinja
from roboviewer.renders._jinja import DEFAULT_DIR, TemplateError
from roboviewer.report import render_report, save
from roboviewer.view import CacheState, build_view

GOLDEN_DIR = Path(__file__).parent / "golden"


def _run(**overrides: object) -> ReviewRun:
    fields: dict[str, object] = {
        "run_id": "20260802-2104-3f1c",
        "repo_root": "/repos/ios-core-ui",
        "branch": "feature/CHATS-16018",
        "target": "develop",
        "base_sha": "abcdef1234567890abcdef",
        "head_sha": "1234567890abcdef123456",
        "model": "qwen3.6-27b",
        "started_at": "2026-08-02T21:04:00",
        "finished_at": "2026-08-02T21:11:32",
        "files": [
            DiffStat(file="Sources/UI/BubbleContentLayout.swift", status="M", added=42, removed=7),
            DiffStat(file="Sources/UI/BubbleReplyBlock.swift", status="M", added=8, removed=120),
            DiffStat(file="Sources/UI/Legacy.swift", status="D", added=0, removed=64),
        ],
    }
    fields.update(overrides)
    return ReviewRun(**fields)  # type: ignore[arg-type]


def full_run() -> ReviewRun:
    """Every branch of the renderer at once: all four severities, a suggestion,
    a judge note, rejected findings, a failed item and a cache that reported hits."""
    findings = [
        Finding(
            id="F1",
            file="Sources/UI/BubbleContentLayout.swift",
            line=88,
            end_line=94,
            severity=Severity.BLOCKER,
            category="correctness",
            title="Половина ширины вместо обрезки",
            rationale="`availableWidth / 2` считается до вычета инсетов, поэтому длинная\nстрока обрезается вместо переноса.",
            suggestion="Считать ширину после `layoutMargins`.",
            confidence=0.82,
            sources=["10-correctness", "80-architecture"],
        ),
        Finding(
            id="F2",
            file="Sources/UI/BubbleReplyBlock.swift",
            line=31,
            severity=Severity.MAJOR,
            category="performance",
            title="Жадный frame(maxWidth: .infinity)",
            rationale="Блок растягивается на всю доступную ширину и ломает выравнивание ответа.",
            confidence=0.6,
            sources=["60-performance"],
        ),
        Finding(
            id="F3",
            file="Sources/UI/BubbleReplyBlock.swift",
            severity=Severity.MINOR,
            category="tests",
            title="Нет теста на пустой reply",
            rationale="Ветка с пустым текстом ответа не покрыта.",
            suggestion="Добавить кейс в `BubbleReplyBlockTests`.",
            confidence=0.45,
            sources=[],
        ),
        Finding(
            id="F4",
            file="Sources/UI/BubbleContentLayout.swift",
            line=12,
            severity=Severity.NIT,
            category="style",
            title="Лишний import",
            rationale="`import Combine` не используется.",
            confidence=0.9,
            sources=["80-architecture"],
        ),
        Finding(
            id="F5",
            file="Sources/UI/Legacy.swift",
            line=5,
            severity=Severity.MAJOR,
            category="security",
            title="Ключ в исходниках",
            rationale="Строка похожа на API-ключ.",
            confidence=0.3,
            sources=["50-security"],
        ),
        Finding(
            id="F6",
            file="Sources/UI/BubbleContentLayout.swift",
            line=90,
            severity=Severity.MINOR,
            category="correctness",
            title="Дубль про ширину",
            rationale="То же, что F1.",
            confidence=0.5,
            sources=["10-correctness"],
        ),
    ]
    verdicts = {
        "F1": Verdict(finding_id="F1", verdict="confirmed", reason="Подтверждено чтением файла целиком."),
        "F2": Verdict(finding_id="F2", verdict="confirmed"),
        "F3": Verdict(finding_id="F3", verdict="nitpick", reason="Тест желателен, но не блокирует."),
        "F4": Verdict(finding_id="F4", verdict="unreviewed", reason="Не проверялось."),
        "F5": Verdict(finding_id="F5", verdict="false_positive", reason="Это идентификатор ресурса, не ключ."),
        "F6": Verdict(finding_id="F6", verdict="duplicate", reason="Совпадает с F1."),
    }
    items = [
        ItemResult(
            item_id="10-correctness",
            item_title="Correctness",
            status="ok",
            findings=findings[:1],
            usage=Usage(prompt_tokens=120_000, completion_tokens=4_200, cached_tokens=96_000, cache_reported=True),
            turns=9,
            duration_s=74.4,
        ),
        ItemResult(
            item_id="30-concurrency",
            item_title="Concurrency",
            status="failed",
            error="the provider returned 502 after 3 attempts",
            usage=Usage(prompt_tokens=24_000, completion_tokens=0, cache_reported=True),
            turns=2,
            duration_s=13.0,
        ),
        ItemResult(
            item_id="70-tests",
            item_title="Tests",
            status="skipped",
            usage=Usage(),
            turns=0,
            duration_s=0.0,
        ),
    ]
    return _run(
        findings=findings,
        verdicts=verdicts,
        items=items,
        judge_summary="Ключевое — расчёт ширины.\nОстальное косметика.",
        judge_usage=Usage(prompt_tokens=18_000, completion_tokens=900, cached_tokens=12_000, cache_reported=True),
    )


def empty_run() -> ReviewRun:
    """Nothing found, and a provider that keeps no cache statistics."""
    return _run(
        items=[
            ItemResult(
                item_id="10-correctness",
                item_title="Correctness",
                status="ok",
                usage=Usage(prompt_tokens=30_000, completion_tokens=1_100),
                turns=4,
                duration_s=22.6,
            )
        ]
    )


def cold_cache_run() -> ReviewRun:
    """The provider does report the number, and it is zero — the one state that
    actually means caching failed."""
    return _run(
        items=[
            ItemResult(
                item_id="10-correctness",
                item_title="Correctness",
                status="ok",
                usage=Usage(prompt_tokens=30_000, completion_tokens=1_100, cache_reported=True),
                turns=4,
                duration_s=22.6,
            )
        ]
    )


CASES = {"full": full_run, "empty": empty_run, "cold-cache": cold_cache_run}


@pytest.mark.parametrize("name", sorted(CASES))
def test_markdown_matches_golden(name: str) -> None:
    expected = (GOLDEN_DIR / f"report-{name}.md").read_text(encoding="utf-8")
    assert render_report(CASES[name]()) == expected


def test_view_keeps_severity_order() -> None:
    view = build_view(full_run())
    assert [row.severity for row in view.stats.by_severity] == [
        Severity.BLOCKER,
        Severity.MAJOR,
        Severity.MINOR,
        Severity.NIT,
    ]


def test_view_separates_rejected_from_confirmed() -> None:
    view = build_view(full_run())
    assert [f.id for f in view.findings] == ["F1", "F2", "F3", "F4"]
    assert [f.id for f in view.rejected] == ["F5", "F6"]


def test_view_hides_reason_of_unreviewed_finding() -> None:
    """An `unreviewed` verdict carries no judgement, so its text must not show up
    as one — the pre-Jinja renderer filtered it and the template relies on that."""
    view = build_view(full_run())
    unreviewed = next(f for f in view.findings if f.id == "F4")
    assert unreviewed.verdict_reason is None


def test_view_tells_the_three_cache_states_apart() -> None:
    assert build_view(full_run()).cache.state is CacheState.HIT
    assert build_view(empty_run()).cache.state is CacheState.UNKNOWN
    assert build_view(cold_cache_run()).cache.state is CacheState.ZERO


# ----------------------------------------------------------- renders and templates


def test_every_render_is_reachable_by_its_name() -> None:
    for name in renders.known():
        assert renders.resolve(name).NAME == name


def test_user_template_overrides_the_bundled_one_file_by_file(tmp_path: Path) -> None:
    """A custom set carries only what it changes: the report is taken from the
    user directory while the macro it imports still comes from the bundle."""
    (tmp_path / "report.md.j2").write_text(
        '{% import "_finding.md.j2" as parts %}\n'
        "{% for finding in findings %}{{ parts.finding(finding) }}{% endfor %}",
        encoding="utf-8",
    )
    text = render_report(full_run(), templates_dir=tmp_path)

    assert text.startswith("### 🛑 F1 · Половина ширины вместо обрезки")
    assert "## Checklist items" not in text


def test_unknown_format_lists_the_ones_that_exist(tmp_path: Path) -> None:
    with pytest.raises(renders.RenderError) as exc:
        render_report(empty_run(), "htlm", tmp_path)
    assert "md" in str(exc.value) and "html" in str(exc.value)


def test_a_format_can_be_added_by_dropping_in_a_template(tmp_path: Path) -> None:
    """A document of your own — a short merge request comment, say — is added
    with one file, no Python involved."""
    (tmp_path / "report.comment.j2").write_text(
        "{{ findings | length }} findings", encoding="utf-8"
    )
    render = renders.resolve("comment", tmp_path)

    assert render.FILENAME == "report.comment"
    assert render.render(full_run(), tmp_path) == "4 findings"


def test_typo_in_a_template_fails_instead_of_rendering_a_blank(tmp_path: Path) -> None:
    """Undefined is strict on purpose: a misspelled field must not ship a report
    with a section quietly missing."""
    (tmp_path / "report.md.j2").write_text("{{ meta.brunch }}", encoding="utf-8")
    with pytest.raises(TemplateError):
        render_report(empty_run(), templates_dir=tmp_path)


def test_missing_template_names_both_places_it_looked(tmp_path: Path) -> None:
    with pytest.raises(TemplateError) as exc:
        _jinja.render_template("nope.md.j2", build_view(empty_run()), tmp_path)
    assert str(tmp_path) in str(exc.value)
    assert str(DEFAULT_DIR) in str(exc.value)


def test_html_escapes_values_and_markdown_does_not(tmp_path: Path) -> None:
    """Escaping follows the target format in the file name. A finding's title is
    model output quoted back, so an HTML report must not take it as markup."""
    run = full_run()
    run.findings[0].title = "<script>alert(1)</script>"
    body = "{{ findings[0].title }}"
    (tmp_path / "report.html.j2").write_text(body, encoding="utf-8")
    (tmp_path / "report.md.j2").write_text(body, encoding="utf-8")

    assert "&lt;script&gt;" in render_report(run, "html", tmp_path)
    assert render_report(run, "md", tmp_path) == "<script>alert(1)</script>"


def test_every_requested_format_is_written_under_its_own_name(tmp_path: Path) -> None:
    reports = save(empty_run(), tmp_path, ["md", "html"])

    assert [p.name for p in reports] == ["report.md", "report.html"]
    assert all(p.is_file() for p in reports)


def test_an_unknown_format_writes_nothing_at_all(tmp_path: Path) -> None:
    """Formats resolve before the first write: otherwise half the reports are
    already on disk while the run counts as failed."""
    with pytest.raises(renders.RenderError):
        save(empty_run(), tmp_path, ["md", "htlm"])
    assert not (tmp_path / "report.md").exists()


def test_a_broken_template_is_caught_before_the_first_write(tmp_path: Path) -> None:
    """Syntax is checked by compiling during preparation, not at write time:
    otherwise the first format is on disk while the second fails the run."""
    templates, out = tmp_path / "templates", tmp_path / "out"
    templates.mkdir()
    (templates / "report.broken.j2").write_text("{% for x in %}", encoding="utf-8")

    with pytest.raises(TemplateError):
        save(empty_run(), out, ["html", "broken"], templates)

    assert not (out / "report.html").exists()


def test_a_template_failing_at_render_time_does_not_lose_the_run(tmp_path: Path) -> None:
    """A run costs money, and the template is the only code here that is edited
    by hand. A mistake in it should cost the readable report, not the results."""
    templates, out = tmp_path / "templates", tmp_path / "out"
    templates.mkdir()
    # Compiles, but trips StrictUndefined at render time
    (templates / "report.md.j2").write_text("{{ meta.brunch }}", encoding="utf-8")

    with pytest.raises(TemplateError):
        save(empty_run(), out, ["md"], templates)

    assert (out / "run.json").is_file()
    assert (out / "findings.json").is_file()
    assert (out / "items").is_dir()


def test_template_error_is_a_render_error() -> None:
    """The caller has one decision to make — "nothing to show" — so it is caught
    as a single type."""
    assert issubclass(TemplateError, renders.RenderError)


# ------------------------------------------------------------------------ HTML


def test_html_report_is_one_self_contained_file() -> None:
    """It gets opened by double click and attached to a ticket, so it must not
    fetch anything: no stylesheet link, no script, no remote image."""
    html = render_report(full_run(), "html")

    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html and "--blocker" in html
    for forbidden in ("<link", "<script", "src=", "@import"):
        assert forbidden not in html


def test_html_renders_the_markdown_inside_a_rationale() -> None:
    """Backticks in the model's prose have to come out as code, and raw HTML in
    the same prose has to stay text."""
    run = full_run()
    run.findings[0].rationale = "Смотри `layoutMargins`, а не <b>это</b>."
    html = render_report(run, "html")

    assert "<code>layoutMargins</code>" in html
    assert "&lt;b&gt;" in html
    assert "<b>это</b>" not in html


def test_html_carries_severity_as_a_class_and_the_id_as_an_anchor() -> None:
    html = render_report(full_run(), "html")
    assert '<article class="finding blocker" id="F1">' in html


def test_html_and_markdown_report_the_same_facts() -> None:
    """Two templates over one view: whatever the numbers are, they agree."""
    run = full_run()
    html, markdown = render_report(run, "html"), render_report(run)

    for fact in ("F1", "Половина ширины вместо обрезки", "qwen3.6-27b", "167 100"):
        assert fact in html and fact in markdown
