"""Persisting run results: markdown for humans, JSON for debugging."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import SEVERITY_LABEL_RU, SEVERITY_ORDER, Finding, ReviewRun, Severity

SEVERITY_ICON = {
    Severity.BLOCKER: "🛑",
    Severity.MAJOR: "⚠️",
    Severity.MINOR: "🔹",
    Severity.NIT: "💬",
}


def _render_finding(run: ReviewRun, finding: Finding) -> str:
    verdict = run.verdicts.get(finding.id)
    lines = [
        f"### {SEVERITY_ICON[finding.severity]} {finding.id} · {finding.title}",
        "",
        f"**Где:** `{finding.location}`  ",
        f"**Важность:** {SEVERITY_LABEL_RU[finding.severity]} · "
        f"**Категория:** {finding.category} · "
        f"**Уверенность:** {finding.confidence:.0%}  ",
        f"**Пункты чек-листа:** {', '.join(finding.sources) or '—'}",
        "",
        finding.rationale,
    ]
    if finding.suggestion:
        lines += ["", f"**Что сделать:** {finding.suggestion}"]
    if verdict and verdict.reason and verdict.verdict != "unreviewed":
        lines += ["", f"> Судья ({verdict.verdict}): {verdict.reason}"]
    return "\n".join(lines)


def _cache_lines(run: ReviewRun) -> list[str]:
    """Prefix-cache stats. The same context block is resent on every turn, so a
    run either costs full price or a fraction of it depending on this number."""
    usage = run.total_usage
    if not usage.prompt_tokens:
        return []
    if usage.cached_tokens:
        share = f"{usage.cache_hit_rate:.0%}"
        saved = f"{usage.cached_tokens:,}".replace(",", " ")
        return [f"- Из кэша: {saved} токенов промпта ({share} входящих)"]
    return [
        "- Из кэша: 0 — кеширование промпта не сработало ни разу.",
        "  Провайдер его не поддерживает, не отдаёт статистику либо префикс каждый раз разный.",
    ]


def render_markdown(run: ReviewRun) -> str:
    confirmed = run.confirmed()
    rejected = run.rejected()
    by_severity = Counter(f.severity for f in confirmed)

    out: list[str] = [
        f"# Ревью {run.branch} → {run.target}",
        "",
        f"- Прогон: `{run.run_id}`",
        f"- База сравнения: `{run.base_sha[:12]}` · HEAD: `{run.head_sha[:12]}`",
        f"- Модель: `{run.model}`",
        f"- Файлов изменено: {len(run.files)} "
        f"(+{sum(f.added for f in run.files)} / -{sum(f.removed for f in run.files)})",
        f"- Токенов: {run.total_usage.total_tokens:,}".replace(",", " "),
        *_cache_lines(run),
        "",
        "## Итог",
        "",
    ]

    if not confirmed:
        out += ["Замечаний нет.", ""]
    else:
        for severity in sorted(by_severity, key=lambda s: SEVERITY_ORDER[s]):
            out.append(
                f"- {SEVERITY_ICON[severity]} {SEVERITY_LABEL_RU[severity]}: {by_severity[severity]}"
            )
        out.append("")

    if run.judge_summary:
        out += ["> " + run.judge_summary.replace("\n", "\n> "), ""]

    if confirmed:
        out += ["## Замечания", ""]
        for finding in confirmed:
            out += [_render_finding(run, finding), ""]

    if rejected:
        out += [
            "<details>",
            f"<summary>Отклонено судьёй ({len(rejected)})</summary>",
            "",
        ]
        for finding in rejected:
            verdict = run.verdicts.get(finding.id)
            reason = f" — {verdict.reason}" if verdict and verdict.reason else ""
            out.append(f"- `{finding.location}` {finding.title} ({verdict.verdict if verdict else '?'}){reason}")
        out += ["", "</details>", ""]

    out += [
        "## Пункты проверки",
        "",
        "| Пункт | Статус | Замечаний | Ходов | Токенов | Из кэша | Время |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in run.items:
        status = {"ok": "✅", "failed": "❌", "skipped": "⏭", "pending": "…", "running": "…"}[item.status]
        cache = f"{item.usage.cache_hit_rate:.0%}" if item.usage.cached_tokens else "—"
        out.append(
            f"| {item.item_title} | {status} | {len(item.findings)} | {item.turns} | "
            f"{item.usage.total_tokens} | {cache} | {item.duration_s:.0f}с |"
        )
    out.append("")

    failed = [i for i in run.items if i.status == "failed"]
    if failed:
        out += ["### Упавшие пункты", ""]
        out += [f"- **{i.item_title}**: {i.error}" for i in failed]
        out.append("")

    out += ["## Изменённые файлы", "", "```"]
    out += [f"{f.status:<3} +{f.added:<5} -{f.removed:<5} {f.file}" for f in run.files]
    out += ["```", ""]

    return "\n".join(out)


def save(run: ReviewRun, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)

    report_path = directory / "report.md"
    report_path.write_text(render_markdown(run), encoding="utf-8")
    (directory / "run.json").write_text(
        run.model_dump_json(indent=2, exclude={"items": {"__all__": {"findings"}}}),
        encoding="utf-8",
    )

    items_dir = directory / "items"
    items_dir.mkdir(exist_ok=True)
    for item in run.items:
        (items_dir / f"{item.item_id}.json").write_text(
            item.model_dump_json(indent=2), encoding="utf-8"
        )

    (directory / "findings.json").write_text(
        json.dumps(
            [
                {
                    **finding.model_dump(mode="json"),
                    "verdict": run.verdicts.get(finding.id, None)
                    and run.verdicts[finding.id].model_dump(mode="json"),
                }
                for finding in run.findings
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    latest = directory.parent / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(directory.name)
    except OSError:
        pass  # the symlink is a convenience, not a requirement

    return report_path
