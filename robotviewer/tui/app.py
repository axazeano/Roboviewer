"""Textual UI: checklist items with statuses on the left, a live log on the right."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from ..checklist import ChecklistItem
from ..config import Config
from ..gitdiff import DiffBundle
from ..models import SEVERITY_LABEL_RU, SEVERITY_ORDER, ReviewRun
from ..pipeline import Event, ReviewPipeline, output_dir_for
from ..report import save
from ..runners import Runner

STATUS_ICON = {
    "pending": "·",
    "running": "▶",
    "ok": "✓",
    "failed": "✗",
    "skipped": "⏭",
}


class ReviewApp(App[ReviewRun]):
    CSS = """
    Screen { layout: vertical; }
    #meta { height: 3; padding: 0 1; color: $text-muted; }
    #body { height: 1fr; }
    #items { width: 50%; min-width: 58; border: round $primary; }
    #log { width: 1fr; border: round $accent; }
    #status { height: 1; padding: 0 1; background: $panel; }
    """

    BINDINGS = [
        ("q", "quit", "Выход"),
        ("o", "open_report", "Открыть отчёт"),
    ]

    status_line: reactive[str] = reactive("Инициализация…")

    def __init__(
        self,
        config: Config,
        diff: DiffBundle,
        items: list[ChecklistItem],
        runner: Runner,
    ) -> None:
        super().__init__()
        self._cfg = config
        self._diff = diff
        self._items = items
        self._runner = runner
        self._row_of: dict[str, int] = {}
        self._report_path: Path | None = None
        self.run_result: ReviewRun | None = None

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        origin = self._cfg.provider.base_url.split("//", 1)[-1].split("/", 1)[0]
        yield Static(
            f"[b]{self._diff.branch}[/b] → [b]{self._diff.target}[/b]  ·  "
            f"merge-base [dim]{self._diff.base_sha[:12]}[/dim]  ·  "
            f"{len(self._diff.files)} файлов\n"
            f"[b]{self._cfg.provider.model}[/b] @ [dim]{origin}[/dim]",
            id="meta",
        )
        with Horizontal(id="body"):
            with Vertical(id="items"):
                yield DataTable(id="items_table", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="log", highlight=True, markup=True, wrap=True, max_lines=4000)
        yield Static(self.status_line, id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#items_table", DataTable)
        # Fixed widths: long titles would otherwise push the right-hand columns out of view
        table.add_column("", width=1)
        table.add_column("Пункт", width=28)
        table.add_column("Замеч.", width=6)
        table.add_column("Токены", width=6)
        table.add_column("Время", width=5)
        for item in self._items:
            key = table.add_row(STATUS_ICON["pending"], item.title, "", "", "", key=item.id)
            self._row_of[item.id] = table.get_row_index(key)
        self.title = "RobotViewer"
        self.sub_title = "автоматическое ревью MR"
        self.run_worker(self._execute(), exclusive=True, name="pipeline")

    def watch_status_line(self, value: str) -> None:
        try:
            self.query_one("#status", Static).update(value)
        except Exception:  # noqa: BLE001 — before the widget is mounted
            pass

    # ---------------------------------------------------------------- events

    def _log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def _set_cell(self, item_id: str, column: int, value: str) -> None:
        table = self.query_one("#items_table", DataTable)
        row = self._row_of.get(item_id)
        if row is None:
            return
        table.update_cell_at((row, column), value)

    def _on_event(self, event: Event) -> None:
        if event.kind == "run_start":
            self.status_line = event.message
            self._log(f"[b cyan]▸[/b cyan] {event.message}")

        elif event.kind == "item_start":
            self._set_cell(event.item_id or "", 0, STATUS_ICON["running"])
            self._log(f"[cyan]▶ {event.message}[/cyan]")

        elif event.kind == "item_progress":
            self._log(f"  [dim]{event.item_id or '-'}[/dim] {event.message}")

        elif event.kind == "item_done":
            result = event.data["result"]
            self._set_cell(result.item_id, 0, STATUS_ICON[result.status])
            self._set_cell(result.item_id, 2, str(len(result.findings)))
            self._set_cell(result.item_id, 3, str(result.usage.total_tokens))
            self._set_cell(result.item_id, 4, f"{result.duration_s:.0f}с")
            colour = "green" if result.status == "ok" else "red"
            self._log(f"[{colour}]{STATUS_ICON[result.status]} {event.message}[/{colour}]")

        elif event.kind == "merge_done":
            self.status_line = event.message
            self._log(f"[b]⇢[/b] {event.message}")

        elif event.kind == "judge_start":
            self.status_line = event.message
            self._log(f"[b magenta]⚖ {event.message}[/b magenta]")

        elif event.kind == "judge_done":
            self._log(f"[b magenta]⚖ {event.message}[/b magenta]")

        elif event.kind == "error":
            self._log(f"[b red]✗ {event.message}[/b red]")

        elif event.kind == "run_done":
            self.status_line = event.message

    # --------------------------------------------------------------- pipeline

    async def _execute(self) -> None:
        pipeline = ReviewPipeline(self._cfg, self._diff, self._items, self._runner, self._on_event)
        try:
            run = await pipeline.execute()
        except Exception as exc:  # noqa: BLE001 — a crash must not take the TUI down
            self._log(f"[b red]Пайплайн упал: {type(exc).__name__}: {exc}[/b red]")
            self.status_line = "Ошибка — нажми q для выхода"
            return
        finally:
            await self._runner.aclose()

        self.run_result = run
        directory = output_dir_for(self._cfg, self._diff.root, run.run_id)
        self._report_path = save(run, directory)

        self._log("")
        self._log(f"[b green]Отчёт:[/b green] {self._report_path}")
        confirmed = run.confirmed()
        if confirmed:
            self._log("")
            for finding in confirmed:
                self._log(
                    f"  [b]{finding.id}[/b] [{_colour(finding.severity)}]"
                    f"{SEVERITY_LABEL_RU[finding.severity]}[/{_colour(finding.severity)}] "
                    f"{finding.location} — {finding.title}"
                )
        self.status_line = (
            f"Готово · подтверждено {len(confirmed)} из {len(run.findings)} · "
            f"{run.total_usage.total_tokens} токенов · o — открыть отчёт, q — выход"
        )

    def action_open_report(self) -> None:
        if self._report_path is None:
            self.notify("Отчёт ещё не готов", severity="warning")
            return
        import subprocess

        subprocess.Popen(["open", str(self._report_path)])


def _colour(severity: object) -> str:
    return {0: "red", 1: "yellow", 2: "cyan", 3: "dim"}[SEVERITY_ORDER[severity]]  # type: ignore[index]
