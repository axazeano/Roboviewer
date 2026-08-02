"""CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import gitdiff
from .checklist import load_checklist
from .config import Config, load_config
from .models import SEVERITY_LABEL_RU, ReviewRun
from .pipeline import Event, ReviewPipeline, output_dir_for
from .prompts import DEFAULT_DIR as PROMPTS_DEFAULT_DIR, PromptError, Prompts
from .report import save
from .runners import OpenAIAgentRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roboviewer",
        description="Локальный автоматический ревьюер merge request'ов на агентах.",
        epilog=(
            "Примеры:\n"
            "  roboviewer develop                     ревью текущей ветки в develop\n"
            "  roboviewer develop feature/login       ревью указанной ветки, выкачивать её не нужно\n"
            "  roboviewer release/2.0 develop         develop в релизную ветку\n"
            "  roboviewer -C ~/work/app develop       репозиторий в другом месте\n"
            "\n"
            "Переменные окружения:\n"
            "  ROBOVIEWER_REPO    репозиторий по умолчанию (если не задан -C)\n"
            "  ROBOVIEWER_OUTPUT  куда складывать отчёты (если не задан --output)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Целевая ветка — куда вливаем (обязательно)",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Исходная ветка — что вливаем (по умолчанию текущая)",
    )
    parser.add_argument(
        "-C",
        "--repo",
        default=os.environ.get("ROBOVIEWER_REPO", "."),
        metavar="ПУТЬ",
        help="Путь к проверяемому репозиторию (по умолчанию $ROBOVIEWER_REPO или текущий каталог)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=os.environ.get("ROBOVIEWER_OUTPUT"),
        metavar="ПУТЬ",
        help=(
            "Куда складывать отчёты. По умолчанию .roboviewer/runs внутри проверяемого "
            "репозитория; укажи путь вне него, чтобы не сорить в рабочем дереве"
        ),
    )
    parser.add_argument("--config", type=Path, help="Явный путь к config.toml")
    parser.add_argument("--checklist", help="Каталог с пунктами чек-листа")
    parser.add_argument("--only", help="Только указанные пункты, через запятую")
    parser.add_argument("--model", help="Переопределить модель")
    parser.add_argument("-j", "--concurrency", type=int, help="Сколько пунктов проверять параллельно")
    parser.add_argument("--no-judge", action="store_true", help="Пропустить финальный прогон судьи")
    parser.add_argument("--no-tui", action="store_true", help="Текстовый вывод вместо TUI")
    parser.add_argument("--list-items", action="store_true", help="Показать пункты чек-листа и выйти")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Показать, какие конфиги подхватились и что получилось после наложения",
    )
    parser.add_argument(
        "--check-provider",
        action="store_true",
        help="Сделать один пробный запрос к провайдеру и разобрать ответ (для отладки 401 и прочего)",
    )
    parser.add_argument("--diff-only", action="store_true", help="Показать сводку диффа и выйти")
    return parser


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    if args.output:
        cfg.run.output_dir = str(Path(args.output).expanduser())
    if args.checklist:
        cfg.run.checklist_dir = args.checklist
    if args.model:
        cfg.provider.model = args.model
    if args.concurrency:
        cfg.run.concurrency = args.concurrency
    if args.no_judge:
        cfg.run.enable_judge = False
    return cfg


def _resolve_checklist_dir(cfg: Config, root: Path) -> Path:
    candidate = Path(cfg.run.checklist_dir).expanduser()
    if candidate.is_absolute():
        return candidate
    package_dir = Path(__file__).resolve().parent
    # A checklist inside the repository wins over the built-in one
    for base in (root, Path.cwd(), package_dir, package_dir.parent):
        resolved = base / candidate
        if resolved.is_dir():
            return resolved
    return root / candidate


def _resolve_prompts_dir(cfg: Config, root: Path) -> Path | None:
    """An explicit `prompts_dir` wins; otherwise a set living inside the reviewed
    repository is picked up on its own. None means the bundled texts."""
    if cfg.run.prompts_dir:
        candidate = Path(cfg.run.prompts_dir).expanduser()
        return candidate if candidate.is_absolute() else root / candidate
    in_repo = root / ".roboviewer" / "prompts"
    return in_repo if in_repo.is_dir() else None


def _load_prompts(cfg: Config, root: Path) -> Prompts:
    directory = _resolve_prompts_dir(cfg, root)
    # A configured directory that does not exist is a typo, not a request for the
    # defaults: the loader would fall back file by file and the run would quietly
    # go out with prompts nobody chose.
    if cfg.run.prompts_dir and (directory is None or not directory.is_dir()):
        raise PromptError(f"Каталог промптов не найден: {directory}")
    return Prompts.load(directory)


def _print_prompt_sources(cfg: Config, root: Path) -> None:
    """Only the overridden templates are named: listing four bundled paths every
    time buries the one line that says the run is not on the default texts."""
    try:
        prompts = _load_prompts(cfg, root)
    except PromptError as exc:
        print(f"  промпты        ошибка: {exc}")
        return

    custom = {name: src for name, src in prompts.sources.items() if Path(src).parent != PROMPTS_DEFAULT_DIR}
    if not custom:
        print("  промпты        из комплекта")
        return
    print(f"  промпты        {_resolve_prompts_dir(cfg, root)}, свои: {len(custom)} из {len(prompts.sources)}")
    for name, src in custom.items():
        print(f"    {name:<14} {src}")


def _print_config(cfg: Config, root: Path) -> None:
    from .config import home_config_path, repo_config_path

    print("Слои конфига (каждый следующий перекрывает предыдущий):")
    known = {str(home_config_path()): "домашний", str(repo_config_path(root)): "репозиторий"}
    for path in cfg.sources:
        print(f"  ✓ {path}   [{known.get(path, 'явный --config')}]")
    for path, label in known.items():
        if path not in cfg.sources:
            print(f"  · {path}   [{label}, нет файла]")
    if not cfg.sources:
        print("  (ни одного файла — всё на значениях по умолчанию)")

    _, key_source = cfg.provider.api_key_source()
    print()
    print("Провайдер:")
    print(f"  base_url     {cfg.provider.base_url}")
    print(f"  model        {cfg.provider.model}")
    print(f"  judge_model  {cfg.provider.resolve_judge_model()}")
    print(f"  ключ         {cfg.provider.masked_key()}")
    print(f"  источник     {key_source}")
    print()
    print("Прогон:")
    print(f"  checklist_dir  {_resolve_checklist_dir(cfg, root)}")
    _print_prompt_sources(cfg, root)
    print(f"  output_dir     {cfg.run.output_dir}")
    print(f"  concurrency    {cfg.run.concurrency}")
    print(f"  max_turns      {cfg.run.max_turns}")
    print(f"  судья          {'включён' if cfg.run.enable_judge else 'выключен'}")


def _print_event(event: Event) -> None:
    if event.kind == "item_progress":
        return  # too noisy for the console
    prefix = {"error": "✗", "item_done": "•", "run_done": "✔"}.get(event.kind, "▸")
    print(f"{prefix} {event.message}", flush=True)


def _print_summary(run: ReviewRun, report_path: Path) -> None:
    print()
    confirmed = run.confirmed()
    for finding in confirmed:
        print(f"  {finding.id}  [{SEVERITY_LABEL_RU[finding.severity]}] {finding.location} — {finding.title}")
    if not confirmed:
        print("  Замечаний нет.")
    print()
    usage = run.total_usage
    cache = f" · из кэша {usage.cache_hit_rate:.0%}" if usage.cached_tokens else " · кэш не сработал"
    print(f"Подтверждено {len(confirmed)} из {len(run.findings)} · "
          f"{usage.total_tokens} токенов{cache}")
    print(f"Отчёт: {report_path}")


async def _run_headless(
    cfg: Config, diff: gitdiff.DiffBundle, items: list, runner, prompts: Prompts
) -> int:
    origin = cfg.provider.base_url.split("//", 1)[-1].split("/", 1)[0]
    print(f"▸ {cfg.provider.model} @ {origin} · конфигов подхвачено: {len(cfg.sources)}")
    pipeline = ReviewPipeline(cfg, diff, items, runner, _print_event, prompts)
    try:
        run = await pipeline.execute()
    finally:
        await runner.aclose()

    report_path = save(run, output_dir_for(cfg, diff.root, run.run_id))
    _print_summary(run, report_path)
    return 1 if any(i.status == "failed" for i in run.items) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Diagnostic commands work without branches and outside a repository
    informational = args.list_items or args.show_config or args.check_provider

    if not args.target and not informational:
        parser.error("не указана целевая ветка: roboviewer <целевая> [исходная]")

    requested = Path(args.repo).expanduser().resolve()
    try:
        root = gitdiff.repo_root(requested)
    except gitdiff.GitError as exc:
        if not informational:
            print(f"Ошибка: {exc}", file=sys.stderr)
            print(
                "Укажи репозиторий через -C ПУТЬ или переменную ROBOVIEWER_REPO.",
                file=sys.stderr,
            )
            return 2
        root = requested

    try:
        cfg = _apply_overrides(load_config(root, args.config), args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка конфига: {exc}", file=sys.stderr)
        return 2

    if args.show_config:
        _print_config(cfg, root)
        return 0

    if args.check_provider:
        from .diagnose import check_provider

        return check_provider(cfg.provider)

    try:
        items = load_checklist(
            _resolve_checklist_dir(cfg, root),
            [s for s in args.only.split(",")] if args.only else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка чек-листа: {exc}", file=sys.stderr)
        return 2

    if args.list_items:
        for item in items:
            print(f"{item.id:<20} {item.title}")
        return 0

    try:
        diff = gitdiff.collect(
            root,
            args.target,
            args.source,
            context_lines=cfg.run.diff_context_lines,
            max_chars=cfg.run.diff_max_chars,
            excludes=cfg.run.exclude_globs,
            inline_max_lines=cfg.run.inline_max_lines,
            inline_max_total_chars=cfg.run.inline_max_total_chars,
        )
    except gitdiff.GitError as exc:
        print(f"Ошибка git: {exc}", file=sys.stderr)
        return 2

    if args.diff_only:
        print(f"{diff.branch} → {diff.target} (merge-base {diff.base_sha[:12]})")
        print(diff.summary_table())
        return 0

    if not diff.files:
        print(f"Изменений в {diff.branch} относительно {diff.target} нет.")
        return 0

    if diff.detached:
        print(f"Ревью ветки {diff.branch} ({diff.head[:12]}); рабочая копия не затрагивается.")

    # Before the runner, so a broken template costs a second rather than a
    # provider connection and eight agents failing one by one
    try:
        prompts = _load_prompts(cfg, root)
        prompts.validate(items, diff)
    except PromptError as exc:
        print(f"Ошибка промптов: {exc}", file=sys.stderr)
        return 2

    try:
        runner = OpenAIAgentRunner(cfg.provider, cfg.run, root, diff.base_sha, diff.source_ref)
    except RuntimeError as exc:
        print(f"Ошибка провайдера: {exc}", file=sys.stderr)
        return 2

    if args.no_tui:
        return asyncio.run(_run_headless(cfg, diff, items, runner, prompts))

    from .tui import ReviewApp

    app = ReviewApp(cfg, diff, items, runner, prompts)
    app.run()
    run = app.run_result
    if run is None:
        return 1
    return 1 if any(i.status == "failed" for i in run.items) else 0


if __name__ == "__main__":
    raise SystemExit(main())
