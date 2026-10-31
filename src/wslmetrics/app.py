from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
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

from wslmetrics.alerts_view import has_hot_alert, render_alerts_text
from wslmetrics.collectors import DockerCollector, HostCollector
from wslmetrics.config import load_config
from wslmetrics.diffs import render_diffs_text
from wslmetrics.formatting import format_bytes, format_percent
from wslmetrics.history import render_history_text
from wslmetrics.loading import (
    LOADING_INTERVAL,
    LoadingAnimator,
    RamLoader,
    SUMMARY_FRAMES,
)
from wslmetrics.models import Snapshot, SortKey, SystemSummary
from wslmetrics.process_detail import ProcessDetailScreen
from wslmetrics.prometheus import write_prom_textfile
from wslmetrics.rates import summary_io_rates
from wslmetrics.sample import enrich_summary_wsl, persist_snapshot
from wslmetrics.system_view import render_system_text, summary_line2


class CopyCellDataTable(DataTable[str]):
    """DataTable with cell cursor; Enter or double-click copies via CellSelected."""

    BINDINGS = [
        Binding("enter", "select_cursor", "Copy", show=True),
        *DataTable.BINDINGS[1:],
    ]

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        kwargs.setdefault("cursor_type", "cell")
        super().__init__(*args, **kwargs)


class SummaryBar(Static):
    """Top summary strip with host (and Docker/WSL) totals."""

    def show_loading(self, message: str | None = None) -> None:
        self.update(message or SUMMARY_FRAMES[0])

    def update_from(
        self,
        summary: SystemSummary,
        *,
        rates: dict[str, float | None] | None = None,
    ) -> None:
        host = (
            f"RAM {format_bytes(summary.mem_used)}/{format_bytes(summary.mem_total)} "
            f"({format_percent(summary.mem_percent)})  ·  "
            f"CPU {format_percent(summary.cpu_percent)}  ·  "
            f"avail {format_bytes(summary.mem_available)}"
        )
        parts = [host]
        if summary.wsl_mem_limit is not None and summary.wsl_mem_percent is not None:
            parts.append(
                f"WSL {format_bytes(summary.mem_used)}/"
                f"{format_bytes(summary.wsl_mem_limit)} "
                f"({format_percent(summary.wsl_mem_percent)})"
            )
        if summary.swap_total > 0 or summary.wsl_swap_limit is not None:
            swap = (
                f"Swap {format_bytes(summary.swap_used)}/"
                f"{format_bytes(summary.swap_total)} "
                f"({format_percent(summary.swap_percent)})"
            )
            if (
                summary.wsl_swap_limit is not None
                and summary.wsl_swap_percent is not None
            ):
                swap += (
                    f" · WSL swap {format_bytes(summary.swap_used)}/"
                    f"{format_bytes(summary.wsl_swap_limit)} "
                    f"({format_percent(summary.wsl_swap_percent)})"
                )
            parts.append(swap)
        if summary.docker_available:
            parts.append(f"Docker mem {format_bytes(summary.docker_mem_total)}")
        else:
            parts.append("Docker unavailable")
        line1 = "  ·  ".join(parts)
        line2 = summary_line2(summary, rates=rates)
        self.update(f"{line1}\n{line2}")


class MonitorApp(App[None]):
    """Live TUI for host processes and Docker container stats."""

    TITLE = "wslmetrics"
    # Avoid autofocus on the hidden filter/session Inputs (steals hotkeys).
    AUTO_FOCUS = None
    CSS = """
    Screen {
        layout: vertical;
    }

    #summary {
        height: 2;
        padding: 0 1;
        background: $surface;
        color: $text;
        text-style: bold;
    }

    #group-badge, #persist-badge, #alert-badge {
        height: 1;
        padding: 0 1;
        color: $text;
        text-style: bold;
        display: none;
    }

    #group-badge {
        background: $warning;
    }

    #persist-badge {
        background: $success;
    }

    #alert-badge {
        background: $error;
    }

    #group-badge.visible, #persist-badge.visible, #alert-badge.visible {
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

    /* Scroll container fills the pane; Static grows with content (charts). */
    #history-scroll, #diffs-scroll, #alerts-scroll, #system-scroll {
        height: 1fr;
    }

    #history-view, #diffs-view, #alerts-view, #system-view {
        height: auto;
        padding: 0 1;
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
        Binding("s", "toggle_persist", "Persist"),
        Binding("t", "edit_top", "Top"),
        Binding("i", "edit_interval", "Interval"),
        Binding("slash", "toggle_filter", "Filter"),
        Binding("d", "process_detail", "Detail"),
        Binding("escape", "cancel_prompt", "Cancel", show=False),
    ]

    sort_key: reactive[SortKey] = reactive(SortKey.CPU)
    filter_text: reactive[str] = reactive("")
    snapshot: reactive[Snapshot | None] = reactive(None)
    group_by_name: reactive[bool] = reactive(False)
    persist_enabled: reactive[bool] = reactive(False)

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
        self._alerts_ready = False
        self._system_ready = False
        self._proc_grouped_columns = False
        self._poll_timer: Timer | None = None
        self._session_field: str | None = None  # "top" | "interval"
        self._last_persist_ts = 0
        self._last_persist_ok: int | None = None
        self._prev_summary: SystemSummary | None = None
        self._prev_summary_mono: float | None = None
        self._io_rates: dict[str, float | None] = {}
        try:
            self._sample_interval = load_config().sample_interval_seconds
        except Exception:  # noqa: BLE001
            self._sample_interval = 30
        # Gates summary bar + center RamLoaders for the initial boot.
        self._boot_loading = LoadingAnimator(SUMMARY_FRAMES)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SummaryBar(id="summary")
        yield Static(
            "GROUP ON — processes aggregated by name (press g to turn off)",
            id="group-badge",
        )
        yield Static(
            "PERSIST ON — writing samples to SQLite (no alerts; press s to turn off)",
            id="persist-badge",
        )
        yield Static(
            "ALERT — threshold fired recently (see Alerts tab)",
            id="alert-badge",
        )
        with Horizontal(id="filter-row"):
            yield Label("Filter:")
            yield Input(placeholder="name…", id="filter-input")
        with Horizontal(id="session-row"):
            yield Label("Value:", id="session-label")
            yield Input(placeholder="", id="session-input")
        with TabbedContent(initial="processes"):
            with TabPane("Processes", id="processes"):
                yield RamLoader(id="proc-loader")
                yield CopyCellDataTable(
                    id="proc-table",
                    zebra_stripes=True,
                    classes="hidden",
                )
            with TabPane("Containers", id="containers"):
                yield RamLoader(id="docker-loader")
                yield CopyCellDataTable(
                    id="docker-table",
                    zebra_stripes=True,
                    classes="hidden",
                )
            with TabPane("System", id="system"):
                yield RamLoader(id="system-loader")
                with VerticalScroll(id="system-scroll", classes="hidden"):
                    yield Static("", id="system-view")
            with TabPane("Diffs", id="diffs"):
                yield RamLoader(id="diffs-loader")
                with VerticalScroll(id="diffs-scroll", classes="hidden"):
                    yield Static("", id="diffs-view")
            with TabPane("History", id="history"):
                yield RamLoader(id="history-loader")
                with VerticalScroll(id="history-scroll", classes="hidden"):
                    yield Static("", id="history-view")
            with TabPane("Alerts", id="alerts"):
                yield RamLoader(id="alerts-loader")
                with VerticalScroll(id="alerts-scroll", classes="hidden"):
                    yield Static("", id="alerts-view")
        yield Static("◈ RAM warming up…", id="status")
        yield Footer()

    def on_mount(self) -> None:
        first = self._boot_loading.start()
        self.query_one("#summary", SummaryBar).show_loading(first)
        self.set_interval(LOADING_INTERVAL, self._tick_boot_loading)

        self._ensure_proc_columns(grouped=False)

        docker_table = self.query_one("#docker-table", DataTable)
        docker_table.add_columns("NAME", "ID", "CPU%", "MEM", "MEM%", "STATUS", "IMAGE")

        # Keep prompt inputs out of the focus cycle until their row is shown.
        self.query_one("#filter-input", Input).can_focus = False
        self.query_one("#session-input", Input).can_focus = False

        self._restart_poll_timer()
        self.collect_snapshot()
        self.refresh_history()
        self.refresh_diffs()
        self.refresh_alerts()
        self.refresh_system()
        self._refresh_alert_badge()

    def _focus_main(self) -> None:
        """Return focus to the live table so app bindings work."""
        try:
            table = self.query_one("#proc-table", DataTable)
            if not table.has_class("hidden"):
                table.focus()
                return
        except Exception:  # noqa: BLE001
            pass
        self.set_focus(None)

    def _focus_scroll(self, scroll_id: str) -> None:
        """Focus a VerticalScroll so PageUp/Down and wheel work on text tabs."""
        try:
            scroll = self.query_one(f"#{scroll_id}", VerticalScroll)
            if not scroll.has_class("hidden"):
                scroll.focus()
        except Exception:  # noqa: BLE001
            pass

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

    def _tick_boot_loading(self) -> None:
        if not self._boot_loading.active:
            return
        self.query_one("#summary", SummaryBar).update(self._boot_loading.tick())
        # Keep summary + center loaders for at least 2s; longer if sample pending.
        if self._boot_loading.should_finish(self.snapshot is not None):
            self._finish_boot_loading()

    def _finish_boot_loading(self) -> None:
        """End boot animation: real summary + reveal live tables."""
        if not self._boot_loading.active:
            return
        self._boot_loading.stop()
        if self.snapshot is not None:
            self.query_one("#summary", SummaryBar).update_from(
                self.snapshot.summary, rates=self._io_rates
            )
        if not self._live_ready:
            self._live_ready = True
        self._hide_live_loading()

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        pane_id = event.pane.id or ""
        self._active_tab = pane_id
        if pane_id == "history":
            self.refresh_history(show_loader=not self._history_ready)
            self._focus_scroll("history-scroll")
        elif pane_id == "diffs":
            self.refresh_diffs(show_loader=not self._diffs_ready)
            self._focus_scroll("diffs-scroll")
        elif pane_id == "alerts":
            self.refresh_alerts(show_loader=not self._alerts_ready)
            self._focus_scroll("alerts-scroll")
        elif pane_id == "system":
            self.refresh_system(show_loader=not self._system_ready)
            self._focus_scroll("system-scroll")
        elif not self._live_ready:
            self._show_live_loading()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        text = str(event.value).strip()
        if not text:
            return
        self.copy_to_clipboard(text)
        self.notify(f"Copied {text}")

    def on_unmount(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def watch_snapshot(self, snapshot: Snapshot | None) -> None:
        if snapshot is None:
            return
        now_mono = time.monotonic()
        dt = (
            now_mono - self._prev_summary_mono
            if self._prev_summary_mono is not None
            else 0.0
        )
        self._io_rates = summary_io_rates(self._prev_summary, snapshot.summary, dt)
        self._prev_summary = snapshot.summary
        self._prev_summary_mono = now_mono

        # During boot loading, leave the summary animation alone.
        if not self._boot_loading.active:
            self.query_one("#summary", SummaryBar).update_from(
                snapshot.summary, rates=self._io_rates
            )
        self._render_processes(snapshot)
        self._render_containers(snapshot)
        self._update_status(snapshot)
        self._history_ticks += 1
        if self._active_tab == "system" or self._history_ticks % 10 == 0:
            self.refresh_system(show_loader=False)
        if self._active_tab == "history" or self._history_ticks % 10 == 0:
            self.refresh_history(show_loader=False)
        if self._active_tab == "diffs" or self._history_ticks % 10 == 0:
            self.refresh_diffs(show_loader=False)
        if self._active_tab == "alerts" or self._history_ticks % 10 == 0:
            self.refresh_alerts(show_loader=False)
            self._refresh_alert_badge()

    def watch_sort_key(self, _: SortKey) -> None:
        if self.snapshot is not None:
            self._render_processes(self.snapshot)
            self._render_containers(self.snapshot)
            self._update_status(self.snapshot)

    def watch_filter_text(self, _: str) -> None:
        if self.snapshot is not None:
            self._render_processes(self.snapshot)
            self._render_containers(self.snapshot)

    def watch_group_by_name(self, _: bool) -> None:
        self._update_header_badges()
        if self.snapshot is not None:
            self._render_processes(self.snapshot)
            self._update_status(self.snapshot)

    def watch_persist_enabled(self, _: bool) -> None:
        self._update_header_badges()
        self._update_status()

    def _update_header_badges(self) -> None:
        group = self.query_one("#group-badge", Static)
        persist = self.query_one("#persist-badge", Static)
        if self.group_by_name:
            group.add_class("visible")
        else:
            group.remove_class("visible")
        if self.persist_enabled:
            persist.add_class("visible")
        else:
            persist.remove_class("visible")
        parts: list[str] = []
        if self.group_by_name:
            parts.append("GROUP ON")
        if self.persist_enabled:
            parts.append("PERSIST ON")
        if has_hot_alert():
            parts.append("ALERT")
        self.sub_title = " · ".join(parts)

    def _refresh_alert_badge(self) -> None:
        badge = self.query_one("#alert-badge", Static)
        if has_hot_alert():
            badge.add_class("visible")
        else:
            badge.remove_class("visible")
        self._update_header_badges()

    @work(exclusive=True, thread=True)
    def collect_snapshot(self) -> None:
        host = self._host
        docker = self._docker
        top = self.top
        sort_key = self.sort_key
        persist = self.persist_enabled
        sample_interval = self._sample_interval

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

        persist_ts: int | None = None
        if persist:
            now = int(time.time())
            if now - self._last_persist_ts >= sample_interval:
                # Align with sample job: top 5 PIDs, top 15 groups, running only.
                running = tuple(
                    c for c in containers if "running" in (c.status or "").lower()
                )
                try:
                    persist_ts = persist_snapshot(
                        summary,
                        processes[:5],
                        running,
                        groups=groups[:15],
                        top_n=5,
                        group_n=15,
                    )
                    try:
                        write_prom_textfile(summary)
                    except Exception:  # noqa: BLE001
                        pass
                    self._last_persist_ts = now
                except Exception:  # noqa: BLE001
                    persist_ts = None

        snap = Snapshot(
            summary=summary,
            processes=processes,
            grouped_processes=groups,
            containers=containers,
        )
        self.call_from_thread(self._apply_snapshot, snap, persist_ts)

    def _apply_snapshot(
        self, snap: Snapshot, persist_ts: int | None = None
    ) -> None:
        if persist_ts is not None:
            self._last_persist_ok = persist_ts
        self.snapshot = snap
        # Sample ready mid-animation: finish only after the minimum loading time.
        if self._boot_loading.should_finish(True):
            self._finish_boot_loading()
        elif not self._boot_loading.active and not self._live_ready:
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
        self._focus_main()

    def _show_history_loading(self) -> None:
        self.query_one("#history-loader", RamLoader).remove_class("hidden")
        self.query_one("#history-scroll", VerticalScroll).add_class("hidden")

    def _hide_history_loading(self) -> None:
        self._history_ready = True
        self.query_one("#history-loader", RamLoader).add_class("hidden")
        self.query_one("#history-scroll", VerticalScroll).remove_class("hidden")

    def _show_diffs_loading(self) -> None:
        self.query_one("#diffs-loader", RamLoader).remove_class("hidden")
        self.query_one("#diffs-scroll", VerticalScroll).add_class("hidden")

    def _hide_diffs_loading(self) -> None:
        self._diffs_ready = True
        self.query_one("#diffs-loader", RamLoader).add_class("hidden")
        self.query_one("#diffs-scroll", VerticalScroll).remove_class("hidden")

    def _show_alerts_loading(self) -> None:
        self.query_one("#alerts-loader", RamLoader).remove_class("hidden")
        self.query_one("#alerts-scroll", VerticalScroll).add_class("hidden")

    def _hide_alerts_loading(self) -> None:
        self._alerts_ready = True
        self.query_one("#alerts-loader", RamLoader).add_class("hidden")
        self.query_one("#alerts-scroll", VerticalScroll).remove_class("hidden")

    def _show_system_loading(self) -> None:
        self.query_one("#system-loader", RamLoader).remove_class("hidden")
        self.query_one("#system-scroll", VerticalScroll).add_class("hidden")

    def _hide_system_loading(self) -> None:
        self._system_ready = True
        self.query_one("#system-loader", RamLoader).add_class("hidden")
        self.query_one("#system-scroll", VerticalScroll).remove_class("hidden")

    def _matches_filter(self, text: str) -> bool:
        needle = self.filter_text.strip().lower()
        if not needle:
            return True
        return needle in text.lower()

    def _capture_table_cursor(
        self, table: DataTable
    ) -> tuple[object | None, int, float, float]:
        """Save row key + column + scroll so a clear/refill can restore focus."""
        scroll_x, scroll_y = table.scroll_x, table.scroll_y
        if table.row_count == 0 or not table.columns:
            return None, 0, scroll_x, scroll_y
        coord = table.cursor_coordinate
        try:
            row_key, _column_key = table.coordinate_to_cell_key(coord)
            return row_key.value, coord.column, scroll_x, scroll_y
        except Exception:  # noqa: BLE001
            return None, coord.column, scroll_x, scroll_y

    def _restore_table_cursor(
        self,
        table: DataTable,
        saved: tuple[object | None, int, float, float],
    ) -> None:
        """Re-apply cursor after refill; follow row key if it still exists."""
        row_key, column, scroll_x, scroll_y = saved
        if table.row_count == 0 or not table.columns:
            return
        column = max(0, min(column, len(table.columns) - 1))
        row = 0
        if row_key is not None:
            try:
                row = table.get_row_index(row_key)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                row = 0
        table.move_cursor(row=row, column=column, animate=False, scroll=False)
        table.scroll_x = scroll_x
        table.scroll_y = scroll_y

    def _render_processes(self, snapshot: Snapshot) -> None:
        table = self.query_one("#proc-table", DataTable)
        saved = self._capture_table_cursor(table)
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
        else:
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

        self._restore_table_cursor(table, saved)

    def _render_containers(self, snapshot: Snapshot) -> None:
        table = self.query_one("#docker-table", DataTable)
        saved = self._capture_table_cursor(table)
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
            self._restore_table_cursor(table, saved)
            return

        if not snapshot.containers:
            table.add_row("(no containers)", "-", "-", "-", "-", "-", "-")
            self._restore_table_cursor(table, saved)
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

        self._restore_table_cursor(table, saved)

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
        if self.persist_enabled:
            if self._last_persist_ok is not None:
                persist = f"persist=ON last={self._last_persist_ok}"
            else:
                persist = f"persist=ON every {self._sample_interval}s"
        else:
            persist = "persist=off"
        self.query_one("#status", Static).update(
            f"sort={sort_label}  ·  mode={mode}  ·  top={self.top} (t)  ·  "
            f"interval={self.interval:g}s (i)  ·  {persist} (s)  ·  "
            f"{filt} (procs+containers)  ·  {docker}  ·  "
            f"d=detail  ·  dbl-click/Enter=copy cell"
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

    @work(exclusive=True, thread=True)
    def refresh_alerts(self, show_loader: bool = True) -> None:
        if show_loader or not self._alerts_ready:
            self.call_from_thread(self._show_alerts_loading)
        text = render_alerts_text()
        self.call_from_thread(self._apply_alerts, text)

    def _apply_alerts(self, text: str) -> None:
        self.query_one("#alerts-view", Static).update(text)
        self._hide_alerts_loading()

    def refresh_system(self, show_loader: bool = True) -> None:
        if show_loader or not self._system_ready:
            self._show_system_loading()
        snap = self.snapshot
        if snap is None:
            text = "System\n(waiting for first sample…)\n"
        else:
            text = render_system_text(snap.summary, rates=self._io_rates)
        self.query_one("#system-view", Static).update(text)
        self._hide_system_loading()

    def action_process_detail(self) -> None:
        if self._active_tab != "processes":
            self.notify("Process detail is for the Processes tab", severity="warning")
            return
        table = self.query_one("#proc-table", DataTable)
        if table.row_count == 0:
            self.notify("No process selected", severity="warning")
            return
        try:
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            key = str(row_key.value)
        except Exception:  # noqa: BLE001
            self.notify("No process selected", severity="warning")
            return

        if key.startswith("g:"):
            name = key[2:]
            matches = self._host.pids_by_name(name)
            self.push_screen(
                ProcessDetailScreen(group_name=name, group_matches=matches)
            )
            return

        try:
            pid = int(key)
        except ValueError:
            self.notify("No process selected", severity="warning")
            return
        detail = self._host.process_detail(pid)
        if detail is None:
            self.notify(f"PID {pid} gone", severity="warning")
            return
        self.push_screen(ProcessDetailScreen(detail=detail))

    def action_refresh(self) -> None:
        if self._active_tab in {"processes", "containers"} and self._live_ready:
            self.query_one("#status", Static).update("◈ RAM refreshing…")
        self.collect_snapshot()
        if self._active_tab == "system":
            self.refresh_system(show_loader=True)
        if self._active_tab == "history":
            self.refresh_history(show_loader=True)
        if self._active_tab == "diffs":
            self.refresh_diffs(show_loader=True)
        if self._active_tab == "alerts":
            self.refresh_alerts(show_loader=True)
            self._refresh_alert_badge()

    def action_sort_cpu(self) -> None:
        self.sort_key = SortKey.CPU
        self.collect_snapshot()

    def action_sort_mem(self) -> None:
        self.sort_key = SortKey.MEM
        self.collect_snapshot()

    def action_toggle_group(self) -> None:
        self.group_by_name = not self.group_by_name

    def action_toggle_persist(self) -> None:
        self.persist_enabled = not self.persist_enabled


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
        inp.can_focus = True
        inp.placeholder = placeholder
        inp.value = value
        inp.focus()

    def _hide_session_row(self) -> None:
        row = self.query_one("#session-row")
        if row.has_class("visible"):
            row.remove_class("visible")
        self._session_field = None
        inp = self.query_one("#session-input", Input)
        inp.value = ""
        inp.can_focus = False
        self._focus_main()

    def _hide_filter_row(self) -> None:
        row = self.query_one("#filter-row")
        if row.has_class("visible"):
            row.remove_class("visible")
        self.filter_text = ""
        inp = self.query_one("#filter-input", Input)
        inp.value = ""
        inp.can_focus = False
        self._focus_main()

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
        inp = self.query_one("#filter-input", Input)
        if row.has_class("visible"):
            inp.can_focus = True
            inp.focus()
        else:
            self.filter_text = ""
            inp.value = ""
            inp.can_focus = False
            self._focus_main()

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
            self._focus_main()
        elif event.input.id == "session-input":
            self._apply_session_value(event.value)
