from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from wslmetrics.collectors import DockerCollector, HostCollector
from wslmetrics.diffs import render_diffs_text
from wslmetrics.formatting import format_bytes, format_percent
from wslmetrics.history import render_history_text
from wslmetrics.loading import RamLoader
from wslmetrics.models import Snapshot, SortKey, SystemSummary
from wslmetrics.sample import enrich_summary_wsl


class SummaryBar(Static):
    """Top summary strip with host (and Docker/WSL) totals."""

    def show_loading(self) -> None:
        self.update("◈ RAM [░░░░░░░░] reading cells…")

    def update_from(self, summary: SystemSummary) -> None:
        host = (
            f"RAM {format_bytes(summary.mem_used)}/{format_bytes(summary.mem_total)} "
            f"({format_percent(summary.mem_percent)})  ·  "
            f"CPU {format_percent(summary.cpu_percent)}  ·  "
            f"avail {format_bytes(summary.mem_available)}"
        )
        if summary.wsl_mem_limit is not None and summary.wsl_mem_percent is not None:
            wsl = (
                f"  ·  WSL {format_bytes(summary.mem_used)}/"
                f"{format_bytes(summary.wsl_mem_limit)} "
                f"({format_percent(summary.wsl_mem_percent)})"
            )
        else:
            wsl = ""
        if summary.docker_available:
            docker = f"  ·  Docker mem {format_bytes(summary.docker_mem_total)}"
        else:
            docker = "  ·  Docker unavailable"
        self.update(host + wsl + docker)


class MonitorApp(App[None]):
    """Live TUI for host processes and Docker container stats."""

    TITLE = "wslmetrics"
    CSS = """
    Screen {
        layout: vertical;
    }

    #summary {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text;
        text-style: bold;
    }

    #group-badge {
        height: 1;
        padding: 0 1;
        background: $warning;
        color: $text;
        text-style: bold;
        display: none;
    }

    #group-badge.visible {
        display: block;
    }

    #filter-row, #session-row {
        height: 3;
        padding: 0 1;
        display: none;
    }

    #filter-row.visible, #session-row.visible {
        display: block;
    }

    #filter-input, #session-input {
        width: 1fr;
    }

    #status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    DataTable {
        height: 1fr;
    }

    #history-view, #diffs-view {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
    }

    .hidden {
        display: none;
    }

    TabbedContent {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "sort_cpu", "Sort CPU"),
        Binding("m", "sort_mem", "Sort MEM"),
        Binding("g", "toggle_group", "Group"),
        Binding("t", "edit_top", "Top"),
        Binding("i", "edit_interval", "Interval"),
        Binding("slash", "toggle_filter", "Filter"),
        Binding("escape", "cancel_prompt", "Cancel", show=False),
    ]

    sort_key: reactive[SortKey] = reactive(SortKey.CPU)
    filter_text: reactive[str] = reactive("")
    snapshot: reactive[Snapshot | None] = reactive(None)
    group_by_name: reactive[bool] = reactive(False)

    def __init__(self, *, interval: float = 1.0, top: int = 30) -> None:
        super().__init__()
        self.interval = max(0.2, interval)
        self.top = max(1, top)
        self._host = HostCollector()
        self._docker = DockerCollector()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="collect")
        self._history_ticks = 0
        self._active_tab = "processes"
        self._live_ready = False
        self._history_ready = False
        self._diffs_ready = False
        self._proc_grouped_columns = False
        self._poll_timer: Timer | None = None
        self._session_field: str | None = None  # "top" | "interval"
        self._summary_frames = (
            "◈ RAM [░░░░░░░░] reading cells…",
            "◈ RAM [▓░░░░░░░] reading cells…",
            "◈ RAM [▓▓░░░░░░] reading cells…",
            "◈ RAM [▓▓▓░░░░░] reading cells…",
            "◈ RAM [▓▓▓▓░░░░] reading cells…",
            "◈ RAM [▓▓▓▓▓░░░] reading cells…",
            "◈ RAM [▓▓▓▓▓▓░░] reading cells…",
            "◈ RAM [▓▓▓▓▓▓▓░] reading cells…",
            "◈ RAM [▓▓▓▓▓▓▓▓] reading cells…",
        )
        self._summary_frame = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar(id="summary")
        yield Static("GROUP ON — processes aggregated by name (press g to turn off)", id="group-badge")
        with Horizontal(id="filter-row"):
            yield Label("Filter:")
            yield Input(placeholder="name…", id="filter-input")
        with Horizontal(id="session-row"):
            yield Label("Value:", id="session-label")
            yield Input(placeholder="", id="session-input")
        with TabbedContent(initial="processes"):
            with TabPane("Processes", id="processes"):
                yield RamLoader(id="proc-loader")
                yield DataTable(
                    id="proc-table",
                    zebra_stripes=True,
                    cursor_type="row",
                    classes="hidden",
                )
            with TabPane("Containers", id="containers"):
                yield RamLoader(id="docker-loader")
                yield DataTable(
                    id="docker-table",
                    zebra_stripes=True,
                    cursor_type="row",
                    classes="hidden",
                )
            with TabPane("Diffs", id="diffs"):
                yield RamLoader(id="diffs-loader")
                yield Static("", id="diffs-view", classes="hidden")
            with TabPane("History", id="history"):
                yield RamLoader(id="history-loader")
                yield Static("", id="history-view", classes="hidden")
        yield Static("◈ RAM warming up…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#summary", SummaryBar).show_loading()
        self.set_interval(0.12, self._tick_summary_loading)

        self._ensure_proc_columns(grouped=False)

        docker_table = self.query_one("#docker-table", DataTable)
        docker_table.add_columns("NAME", "ID", "CPU%", "MEM", "MEM%", "STATUS", "IMAGE")

        self._restart_poll_timer()
        self.collect_snapshot()
        self.refresh_history()
        self.refresh_diffs()

    def _restart_poll_timer(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
        self._poll_timer = self.set_interval(self.interval, self.collect_snapshot)

    def _ensure_proc_columns(self, *, grouped: bool) -> None:
        table = self.query_one("#proc-table", DataTable)
        if grouped == self._proc_grouped_columns and table.columns:
            return
        table.clear(columns=True)
        if grouped:
            table.add_columns("NAME", "N", "CPU%", "MEM%", "RSS", "USER")
        else:
            table.add_columns("NAME", "PID", "CPU%", "MEM%", "RSS", "USER")
        self._proc_grouped_columns = grouped

    def _tick_summary_loading(self) -> None:
        if self._live_ready:
            return
        self._summary_frame = (self._summary_frame + 1) % len(self._summary_frames)
        self.query_one("#summary", SummaryBar).update(
            self._summary_frames[self._summary_frame]
        )

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        pane_id = event.pane.id or ""
        self._active_tab = pane_id
        if pane_id == "history":
            self.refresh_history(show_loader=not self._history_ready)
        elif pane_id == "diffs":
            self.refresh_diffs(show_loader=not self._diffs_ready)
        elif not self._live_ready:
            self._show_live_loading()

    def on_unmount(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def watch_snapshot(self, snapshot: Snapshot | None) -> None:
        if snapshot is None:
            return
        self.query_one("#summary", SummaryBar).update_from(snapshot.summary)
        self._render_processes(snapshot)
        self._render_containers(snapshot)
        self._update_status(snapshot)
        self._history_ticks += 1
        if self._active_tab == "history" or self._history_ticks % 10 == 0:
            self.refresh_history(show_loader=False)
        if self._active_tab == "diffs" or self._history_ticks % 10 == 0:
            self.refresh_diffs(show_loader=False)

    def watch_sort_key(self, _: SortKey) -> None:
        if self.snapshot is not None:
            self._render_processes(self.snapshot)
            self._render_containers(self.snapshot)
            self._update_status(self.snapshot)

    def watch_filter_text(self, _: str) -> None:
        if self.snapshot is not None:
            self._render_processes(self.snapshot)
            self._render_containers(self.snapshot)

    def watch_group_by_name(self, grouped: bool) -> None:
        badge = self.query_one("#group-badge", Static)
        if grouped:
            badge.add_class("visible")
            self.sub_title = "GROUP ON"
        else:
            badge.remove_class("visible")
            self.sub_title = ""
        if self.snapshot is not None:
            self._render_processes(self.snapshot)
            self._update_status(self.snapshot)

    @work(exclusive=True, thread=True)
    def collect_snapshot(self) -> None:
        host = self._host
        docker = self._docker
        top = self.top
        sort_key = self.sort_key

        future_summary = self._executor.submit(host.summary)
        future_docker = self._executor.submit(docker.collect)

        summary = enrich_summary_wsl(future_summary.result())
        containers, docker_mem = future_docker.result()
        summary = replace(
            summary,
            docker_mem_total=docker_mem,
            docker_available=docker.available,
            docker_error=docker.error,
        )

        future_procs = self._executor.submit(host.processes, top=top, sort_key=sort_key)
        future_groups = self._executor.submit(
            host.grouped_processes,
            top=top,
            sort_key=sort_key,
            mem_total=summary.mem_total,
        )
        processes = future_procs.result()
        groups = future_groups.result()
        snap = Snapshot(
            summary=summary,
            processes=processes,
            grouped_processes=groups,
            containers=containers,
        )
        self.call_from_thread(self._apply_snapshot, snap)

    def _apply_snapshot(self, snap: Snapshot) -> None:
        self.snapshot = snap
        if not self._live_ready:
            self._live_ready = True
            self._hide_live_loading()

    def _show_live_loading(self) -> None:
        self.query_one("#proc-loader", RamLoader).remove_class("hidden")
        self.query_one("#docker-loader", RamLoader).remove_class("hidden")
        self.query_one("#proc-table", DataTable).add_class("hidden")
        self.query_one("#docker-table", DataTable).add_class("hidden")

    def _hide_live_loading(self) -> None:
        self.query_one("#proc-loader", RamLoader).add_class("hidden")
        self.query_one("#docker-loader", RamLoader).add_class("hidden")
        self.query_one("#proc-table", DataTable).remove_class("hidden")
        self.query_one("#docker-table", DataTable).remove_class("hidden")

    def _show_history_loading(self) -> None:
        self.query_one("#history-loader", RamLoader).remove_class("hidden")
        self.query_one("#history-view", Static).add_class("hidden")

    def _hide_history_loading(self) -> None:
        self._history_ready = True
        self.query_one("#history-loader", RamLoader).add_class("hidden")
        self.query_one("#history-view", Static).remove_class("hidden")

    def _show_diffs_loading(self) -> None:
        self.query_one("#diffs-loader", RamLoader).remove_class("hidden")
        self.query_one("#diffs-view", Static).add_class("hidden")

    def _hide_diffs_loading(self) -> None:
        self._diffs_ready = True
        self.query_one("#diffs-loader", RamLoader).add_class("hidden")
        self.query_one("#diffs-view", Static).remove_class("hidden")

    def _matches_filter(self, text: str) -> bool:
        needle = self.filter_text.strip().lower()
        if not needle:
            return True
        return needle in text.lower()

    def _render_processes(self, snapshot: Snapshot) -> None:
        table = self.query_one("#proc-table", DataTable)
        grouped = self.group_by_name
        self._ensure_proc_columns(grouped=grouped)
        table.clear()

        if grouped:
            rows = list(snapshot.grouped_processes)
            if self.sort_key is SortKey.MEM:
                rows.sort(key=lambda r: (r.rss_bytes, r.cpu_percent), reverse=True)
            else:
                rows.sort(key=lambda r: (r.cpu_percent, r.rss_bytes), reverse=True)
            for row in rows:
                if not self._matches_filter(f"{row.name} {row.username}"):
                    continue
                table.add_row(
                    row.name,
                    str(row.count),
                    f"{row.cpu_percent:.1f}",
                    f"{row.mem_percent:.1f}",
                    format_bytes(row.rss_bytes),
                    row.username,
                    key=f"g:{row.name}",
                )
            return

        rows = list(snapshot.processes)
        if self.sort_key is SortKey.MEM:
            rows.sort(key=lambda r: (r.rss_bytes, r.cpu_percent), reverse=True)
        else:
            rows.sort(key=lambda r: (r.cpu_percent, r.rss_bytes), reverse=True)

        for row in rows:
            if not self._matches_filter(f"{row.name} {row.username} {row.pid}"):
                continue
            table.add_row(
                row.name,
                str(row.pid),
                f"{row.cpu_percent:.1f}",
                f"{row.mem_percent:.1f}",
                format_bytes(row.rss_bytes),
                row.username,
                key=str(row.pid),
            )

    def _render_containers(self, snapshot: Snapshot) -> None:
        table = self.query_one("#docker-table", DataTable)
        table.clear()

        if not snapshot.summary.docker_available:
            table.add_row(
                "(docker unavailable)",
                "-",
                "-",
                "-",
                "-",
                snapshot.summary.docker_error or "offline",
                "-",
            )
            return

        if not snapshot.containers:
            table.add_row("(no containers)", "-", "-", "-", "-", "-", "-")
            return

        rows = list(snapshot.containers)
        if self.sort_key is SortKey.MEM:
            rows.sort(
                key=lambda r: (r.mem_usage_bytes, r.cpu_percent),
                reverse=True,
            )
        else:
            rows.sort(
                key=lambda r: (r.cpu_percent, r.mem_usage_bytes),
                reverse=True,
            )

        for row in rows:
            if not self._matches_filter(f"{row.name} {row.image} {row.short_id}"):
                continue
            mem = (
                f"{format_bytes(row.mem_usage_bytes)}/{format_bytes(row.mem_limit_bytes)}"
                if row.mem_limit_bytes
                else format_bytes(row.mem_usage_bytes)
            )
            table.add_row(
                row.name,
                row.short_id,
                f"{row.cpu_percent:.1f}",
                mem,
                f"{row.mem_percent:.1f}",
                row.status,
                row.image,
                key=row.short_id,
            )

    def _update_status(self, snapshot: Snapshot | None = None) -> None:
        snap = snapshot if snapshot is not None else self.snapshot
        sort_label = "CPU" if self.sort_key is SortKey.CPU else "MEM"
        mode = "GROUP ON" if self.group_by_name else "pid"
        filt = f'filter="{self.filter_text}"' if self.filter_text.strip() else "filter=off"
        if snap is None:
            docker = "…"
        elif snap.summary.docker_available:
            docker = f"{len(snap.containers)} containers"
        else:
            docker = f"docker: {snap.summary.docker_error or 'unavailable'}"
        self.query_one("#status", Static).update(
            f"sort={sort_label}  ·  mode={mode}  ·  top={self.top} (t)  ·  "
            f"interval={self.interval:g}s (i)  ·  {filt} (procs+containers)  ·  {docker}"
        )

    @work(exclusive=True, thread=True)
    def refresh_history(self, show_loader: bool = True) -> None:
        if show_loader or not self._history_ready:
            self.call_from_thread(self._show_history_loading)
        size = self.size
        width = max(40, size.width - 4)
        height = max(6, min(10, (size.height - 8) // 2))
        text = render_history_text(width=width, height=height)
        self.call_from_thread(self._apply_history, text)

    def _apply_history(self, text: str) -> None:
        self.query_one("#history-view", Static).update(text)
        self._hide_history_loading()

    @work(exclusive=True, thread=True)
    def refresh_diffs(self, show_loader: bool = True) -> None:
        if show_loader or not self._diffs_ready:
            self.call_from_thread(self._show_diffs_loading)
        text = render_diffs_text(top=10)
        self.call_from_thread(self._apply_diffs, text)

    def _apply_diffs(self, text: str) -> None:
        self.query_one("#diffs-view", Static).update(text)
        self._hide_diffs_loading()

    def action_refresh(self) -> None:
        if self._active_tab in {"processes", "containers"} and self._live_ready:
            self.query_one("#status", Static).update("◈ RAM refreshing…")
        self.collect_snapshot()
        if self._active_tab == "history":
            self.refresh_history(show_loader=True)
        if self._active_tab == "diffs":
            self.refresh_diffs(show_loader=True)

    def action_sort_cpu(self) -> None:
        self.sort_key = SortKey.CPU
        self.collect_snapshot()

    def action_sort_mem(self) -> None:
        self.sort_key = SortKey.MEM
        self.collect_snapshot()

    def action_toggle_group(self) -> None:
        self.group_by_name = not self.group_by_name

    def action_edit_top(self) -> None:
        self._open_session_prompt(
            field="top",
            label="Top N:",
            placeholder=str(self.top),
            value=str(self.top),
        )

    def action_edit_interval(self) -> None:
        self._open_session_prompt(
            field="interval",
            label="Interval (s):",
            placeholder=str(self.interval),
            value=f"{self.interval:g}",
        )

    def _open_session_prompt(
        self,
        *,
        field: str,
        label: str,
        placeholder: str,
        value: str,
    ) -> None:
        self._hide_filter_row()
        self._session_field = field
        row = self.query_one("#session-row")
        row.add_class("visible")
        self.query_one("#session-label", Label).update(label)
        inp = self.query_one("#session-input", Input)
        inp.placeholder = placeholder
        inp.value = value
        inp.focus()

    def _hide_session_row(self) -> None:
        row = self.query_one("#session-row")
        if row.has_class("visible"):
            row.remove_class("visible")
        self._session_field = None
        self.query_one("#session-input", Input).value = ""

    def _hide_filter_row(self) -> None:
        row = self.query_one("#filter-row")
        if row.has_class("visible"):
            row.remove_class("visible")
        self.filter_text = ""
        self.query_one("#filter-input", Input).value = ""

    def _apply_session_value(self, raw: str) -> None:
        field = self._session_field
        self._hide_session_row()
        text = raw.strip()
        if not text or field is None:
            return
        try:
            if field == "top":
                self.top = max(1, min(500, int(text)))
                self.collect_snapshot()
            elif field == "interval":
                self.interval = max(0.2, min(60.0, float(text)))
                self._restart_poll_timer()
        except ValueError:
            pass
        self._update_status()

    def action_toggle_filter(self) -> None:
        self._hide_session_row()
        row = self.query_one("#filter-row")
        row.toggle_class("visible")
        if row.has_class("visible"):
            self.query_one("#filter-input", Input).focus()
        else:
            self.filter_text = ""
            self.query_one("#filter-input", Input).value = ""

    def action_cancel_prompt(self) -> None:
        filter_row = self.query_one("#filter-row")
        session_row = self.query_one("#session-row")
        if session_row.has_class("visible"):
            self._hide_session_row()
            return
        if filter_row.has_class("visible"):
            self._hide_filter_row()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.filter_text = event.value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            self.filter_text = event.value
            self.query_one("#proc-table", DataTable).focus()
        elif event.input.id == "session-input":
            self._apply_session_value(event.value)
            self.query_one("#proc-table", DataTable).focus()
