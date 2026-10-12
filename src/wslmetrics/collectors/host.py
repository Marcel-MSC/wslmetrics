from __future__ import annotations

from collections import defaultdict

import psutil

from wslmetrics.models import GroupedProcessRow, ProcessRow, SortKey, SystemSummary


class HostCollector:
    """Collect host CPU/RAM and top processes via psutil."""

    def __init__(self, *, prime_processes: bool = True) -> None:
        # Prime cpu_percent so the first non-blocking sample is meaningful.
        psutil.cpu_percent(interval=None)
        self._processes_primed = False
        if prime_processes:
            self._prime_processes()

    def _prime_processes(self) -> None:
        if self._processes_primed:
            return
        for proc in psutil.process_iter(["pid"]):
            try:
                proc.cpu_percent(interval=None)
            except (psutil.Error, ProcessLookupError):
                continue
        self._processes_primed = True

    def summary(self) -> SystemSummary:
        mem = psutil.virtual_memory()
        return SystemSummary(
            mem_total=mem.total,
            mem_used=mem.used,
            mem_available=mem.available,
            mem_percent=mem.percent,
            cpu_percent=psutil.cpu_percent(interval=None),
        )

    def _iter_process_rows(self) -> list[ProcessRow]:
        rows: list[ProcessRow] = []
        for proc in psutil.process_iter(
            ["pid", "name", "username", "memory_info", "memory_percent"]
        ):
            try:
                info = proc.info
                mem_info = info.get("memory_info")
                if mem_info is None:
                    continue
                rss = int(getattr(mem_info, "rss", 0) or 0)
                rows.append(
                    ProcessRow(
                        pid=int(info["pid"]),
                        name=str(info.get("name") or "?"),
                        cpu_percent=float(proc.cpu_percent(interval=None) or 0.0),
                        mem_percent=float(info.get("memory_percent") or 0.0),
                        rss_bytes=rss,
                        username=str(info.get("username") or "?"),
                    )
                )
            except (psutil.Error, ProcessLookupError, TypeError, ValueError):
                continue
        return rows

    def processes(
        self,
        *,
        top: int = 30,
        sort_key: SortKey = SortKey.CPU,
    ) -> tuple[ProcessRow, ...]:
        rows = self._iter_process_rows()
        if sort_key is SortKey.MEM:
            rows.sort(key=lambda r: (r.rss_bytes, r.cpu_percent), reverse=True)
        else:
            rows.sort(key=lambda r: (r.cpu_percent, r.rss_bytes), reverse=True)
        return tuple(rows[: max(1, top)])

    def grouped_processes(
        self,
        *,
        top: int = 30,
        sort_key: SortKey = SortKey.MEM,
        mem_total: int | None = None,
    ) -> tuple[GroupedProcessRow, ...]:
        rows = self._iter_process_rows()
        if mem_total is None:
            mem_total = int(psutil.virtual_memory().total) or 1

        buckets: dict[str, dict] = defaultdict(
            lambda: {"count": 0, "cpu": 0.0, "rss": 0, "users": set()}
        )
        for row in rows:
            b = buckets[row.name]
            b["count"] += 1
            b["cpu"] += row.cpu_percent
            b["rss"] += row.rss_bytes
            b["users"].add(row.username)

        grouped: list[GroupedProcessRow] = []
        for name, b in buckets.items():
            users = b["users"]
            username = next(iter(users)) if len(users) == 1 else "*"
            rss = int(b["rss"])
            grouped.append(
                GroupedProcessRow(
                    name=name,
                    count=int(b["count"]),
                    cpu_percent=float(b["cpu"]),
                    mem_percent=(rss / mem_total) * 100.0 if mem_total else 0.0,
                    rss_bytes=rss,
                    username=username,
                )
            )

        if sort_key is SortKey.CPU:
            grouped.sort(key=lambda r: (r.cpu_percent, r.rss_bytes), reverse=True)
        else:
            grouped.sort(key=lambda r: (r.rss_bytes, r.cpu_percent), reverse=True)
        return tuple(grouped[: max(1, top)])
