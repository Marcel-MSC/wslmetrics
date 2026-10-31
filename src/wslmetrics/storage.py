from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from wslmetrics.models import (
    ContainerDelta,
    ContainerRow,
    DiskMount,
    GroupedProcessRow,
    ProcessDelta,
    ProcessRow,
    SystemSummary,
)
from wslmetrics.paths import DB_PATH, ensure_dirs

RAW_RETENTION_S = 24 * 3600
MED_RETENTION_S = 7 * 24 * 3600
COARSE_RETENTION_S = 30 * 24 * 3600
BUCKET_5M = 5 * 60
BUCKET_1H = 3600

_EXTRA_SAMPLE_COLS: tuple[tuple[str, str], ...] = (
    ("cpu_queue_1m", "REAL NOT NULL DEFAULT 0"),
    ("disk_root_percent", "REAL"),
    ("disk_mntc_percent", "REAL"),
    ("disk_read_bytes", "INTEGER NOT NULL DEFAULT 0"),
    ("disk_write_bytes", "INTEGER NOT NULL DEFAULT 0"),
    ("net_rx_bytes", "INTEGER NOT NULL DEFAULT 0"),
    ("net_tx_bytes", "INTEGER NOT NULL DEFAULT 0"),
)

# Old → new column renames (applied before _ensure_columns).
_RENAME_SAMPLE_COLS: tuple[tuple[str, str], ...] = (
    ("load1", "cpu_queue_1m"),
)


@dataclass(frozen=True, slots=True)
class SamplePoint:
    ts: int
    cpu: float
    mem_percent: float
    mem_used: int
    mem_total: int
    docker_mem: int
    cpu_queue_1m: float = 0.0
    disk_root_percent: float | None = None
    disk_mntc_percent: float | None = None
    disk_read_bytes: int = 0
    disk_write_bytes: int = 0
    net_rx_bytes: int = 0
    net_tx_bytes: int = 0


def _mount_percent(mounts: tuple[DiskMount, ...], path: str) -> float | None:
    for m in mounts:
        if m.path == path:
            return float(m.percent)
    return None


def _sample_extras(summary: SystemSummary) -> dict:
    disk_io = summary.disk_io
    net_io = summary.net_io
    return {
        "cpu_queue_1m": float(summary.cpu_queue_1m),
        "disk_root_percent": _mount_percent(summary.disk_mounts, "/"),
        "disk_mntc_percent": _mount_percent(summary.disk_mounts, "/mnt/c"),
        "disk_read_bytes": int(disk_io.read_bytes) if disk_io else 0,
        "disk_write_bytes": int(disk_io.write_bytes) if disk_io else 0,
        "net_rx_bytes": int(net_io.rx_bytes) if net_io else 0,
        "net_tx_bytes": int(net_io.tx_bytes) if net_io else 0,
    }


class MetricsStore:
    """SQLite store with raw + downsampled history."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            ensure_dirs()
            self.path = DB_PATH
        else:
            self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MetricsStore:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS samples (
                ts INTEGER PRIMARY KEY,
                cpu REAL NOT NULL,
                mem_percent REAL NOT NULL,
                mem_used INTEGER NOT NULL,
                mem_total INTEGER NOT NULL,
                docker_mem INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS samples_5m (
                ts INTEGER PRIMARY KEY,
                cpu REAL NOT NULL,
                mem_percent REAL NOT NULL,
                mem_used INTEGER NOT NULL,
                mem_total INTEGER NOT NULL,
                docker_mem INTEGER NOT NULL DEFAULT 0,
                n INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS samples_1h (
                ts INTEGER PRIMARY KEY,
                cpu REAL NOT NULL,
                mem_percent REAL NOT NULL,
                mem_used INTEGER NOT NULL,
                mem_total INTEGER NOT NULL,
                docker_mem INTEGER NOT NULL DEFAULT 0,
                n INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS top_processes (
                ts INTEGER NOT NULL,
                rank INTEGER NOT NULL,
                name TEXT NOT NULL,
                pid INTEGER NOT NULL,
                cpu REAL NOT NULL,
                rss INTEGER NOT NULL,
                PRIMARY KEY (ts, rank)
            );
            CREATE TABLE IF NOT EXISTS containers (
                ts INTEGER NOT NULL,
                name TEXT NOT NULL,
                cpu REAL NOT NULL,
                mem INTEGER NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (ts, name)
            );
            CREATE TABLE IF NOT EXISTS process_groups (
                ts INTEGER NOT NULL,
                name TEXT NOT NULL,
                count INTEGER NOT NULL,
                cpu REAL NOT NULL,
                rss INTEGER NOT NULL,
                PRIMARY KEY (ts, name)
            );
            CREATE INDEX IF NOT EXISTS idx_top_processes_ts ON top_processes(ts);
            CREATE INDEX IF NOT EXISTS idx_containers_ts ON containers(ts);
            CREATE INDEX IF NOT EXISTS idx_process_groups_ts ON process_groups(ts);
            """
        )
        for table in ("samples", "samples_5m", "samples_1h"):
            self._rename_columns(table, _RENAME_SAMPLE_COLS)
            self._ensure_columns(table, _EXTRA_SAMPLE_COLS)
        self._conn.commit()

    def _table_columns(self, table: str) -> set[str]:
        return {
            str(r["name"])
            for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _rename_columns(
        self, table: str, renames: Sequence[tuple[str, str]]
    ) -> None:
        existing = self._table_columns(table)
        for old, new in renames:
            if old in existing and new not in existing:
                self._conn.execute(
                    f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}"
                )
                existing.discard(old)
                existing.add(new)

    def _ensure_columns(
        self, table: str, columns: Sequence[tuple[str, str]]
    ) -> None:
        existing = self._table_columns(table)
        for name, decl in columns:
            if name in existing:
                continue
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def insert_sample(
        self,
        summary: SystemSummary,
        processes: Sequence[ProcessRow],
        containers: Sequence[ContainerRow],
        *,
        groups: Sequence[GroupedProcessRow] = (),
        ts: int | None = None,
        top_n: int = 5,
        group_n: int = 15,
    ) -> int:
        now = int(ts if ts is not None else time.time())
        extras = _sample_extras(summary)
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO samples
                    (ts, cpu, mem_percent, mem_used, mem_total, docker_mem,
                     cpu_queue_1m, disk_root_percent, disk_mntc_percent,
                     disk_read_bytes, disk_write_bytes, net_rx_bytes, net_tx_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    float(summary.cpu_percent),
                    float(summary.mem_percent),
                    int(summary.mem_used),
                    int(summary.mem_total),
                    int(summary.docker_mem_total),
                    extras["cpu_queue_1m"],
                    extras["disk_root_percent"],
                    extras["disk_mntc_percent"],
                    extras["disk_read_bytes"],
                    extras["disk_write_bytes"],
                    extras["net_rx_bytes"],
                    extras["net_tx_bytes"],
                ),
            )
            self._conn.execute("DELETE FROM top_processes WHERE ts = ?", (now,))
            for rank, proc in enumerate(processes[:top_n], start=1):
                self._conn.execute(
                    """
                    INSERT INTO top_processes (ts, rank, name, pid, cpu, rss)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        rank,
                        proc.name,
                        proc.pid,
                        float(proc.cpu_percent),
                        int(proc.rss_bytes),
                    ),
                )
            self._conn.execute("DELETE FROM process_groups WHERE ts = ?", (now,))
            for group in list(groups)[:group_n]:
                self._conn.execute(
                    """
                    INSERT INTO process_groups (ts, name, count, cpu, rss)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        group.name,
                        int(group.count),
                        float(group.cpu_percent),
                        int(group.rss_bytes),
                    ),
                )
            self._conn.execute("DELETE FROM containers WHERE ts = ?", (now,))
            for row in containers:
                if row.status != "running":
                    continue
                self._conn.execute(
                    """
                    INSERT INTO containers (ts, name, cpu, mem, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        row.name,
                        float(row.cpu_percent),
                        int(row.mem_usage_bytes),
                        row.status,
                    ),
                )
        return now

    def maintain(self, *, now: int | None = None) -> None:
        """Downsample old raw/5m data and enforce retention."""
        now = int(now if now is not None else time.time())
        raw_cutoff = now - RAW_RETENTION_S
        med_cutoff = now - MED_RETENTION_S
        coarse_cutoff = now - COARSE_RETENTION_S

        with self._conn:
            self._aggregate_into(
                source="samples",
                dest="samples_5m",
                bucket=BUCKET_5M,
                older_than=raw_cutoff,
            )
            self._conn.execute("DELETE FROM samples WHERE ts < ?", (raw_cutoff,))
            self._conn.execute("DELETE FROM top_processes WHERE ts < ?", (raw_cutoff,))
            self._conn.execute("DELETE FROM process_groups WHERE ts < ?", (raw_cutoff,))
            self._conn.execute("DELETE FROM containers WHERE ts < ?", (raw_cutoff,))

            self._aggregate_into(
                source="samples_5m",
                dest="samples_1h",
                bucket=BUCKET_1H,
                older_than=med_cutoff,
            )
            self._conn.execute("DELETE FROM samples_5m WHERE ts < ?", (med_cutoff,))
            self._conn.execute("DELETE FROM samples_1h WHERE ts < ?", (coarse_cutoff,))

    def _aggregate_into(
        self,
        *,
        source: str,
        dest: str,
        bucket: int,
        older_than: int,
    ) -> None:
        rows = self._conn.execute(
            f"""
            SELECT
                (ts / ?) * ? AS bucket_ts,
                AVG(cpu) AS cpu,
                AVG(mem_percent) AS mem_percent,
                AVG(mem_used) AS mem_used,
                AVG(mem_total) AS mem_total,
                AVG(docker_mem) AS docker_mem,
                AVG(cpu_queue_1m) AS cpu_queue_1m,
                AVG(disk_root_percent) AS disk_root_percent,
                AVG(disk_mntc_percent) AS disk_mntc_percent,
                AVG(disk_read_bytes) AS disk_read_bytes,
                AVG(disk_write_bytes) AS disk_write_bytes,
                AVG(net_rx_bytes) AS net_rx_bytes,
                AVG(net_tx_bytes) AS net_tx_bytes,
                COUNT(*) AS n
            FROM {source}
            WHERE ts < ?
            GROUP BY bucket_ts
            """,
            (bucket, bucket, older_than),
        ).fetchall()
        for row in rows:
            existing = self._conn.execute(
                f"""
                SELECT n, cpu, mem_percent, mem_used, mem_total, docker_mem,
                       cpu_queue_1m, disk_root_percent, disk_mntc_percent,
                       disk_read_bytes, disk_write_bytes, net_rx_bytes, net_tx_bytes
                FROM {dest} WHERE ts = ?
                """,
                (row["bucket_ts"],),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    f"""
                    INSERT INTO {dest}
                        (ts, cpu, mem_percent, mem_used, mem_total, docker_mem,
                         cpu_queue_1m, disk_root_percent, disk_mntc_percent,
                         disk_read_bytes, disk_write_bytes, net_rx_bytes, net_tx_bytes, n)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["bucket_ts"],
                        row["cpu"],
                        row["mem_percent"],
                        int(row["mem_used"]),
                        int(row["mem_total"]),
                        int(row["docker_mem"]),
                        float(row["cpu_queue_1m"] or 0.0),
                        row["disk_root_percent"],
                        row["disk_mntc_percent"],
                        int(row["disk_read_bytes"] or 0),
                        int(row["disk_write_bytes"] or 0),
                        int(row["net_rx_bytes"] or 0),
                        int(row["net_tx_bytes"] or 0),
                        int(row["n"]),
                    ),
                )
            else:
                n_old = int(existing["n"])
                n_new = int(row["n"])
                n_total = n_old + n_new

                def _avg(old_v, new_v):
                    if old_v is None and new_v is None:
                        return None
                    o = 0.0 if old_v is None else float(old_v)
                    n = 0.0 if new_v is None else float(new_v)
                    if old_v is None:
                        return n
                    if new_v is None:
                        return o
                    return (o * n_old + n * n_new) / n_total

                self._conn.execute(
                    f"""
                    UPDATE {dest}
                    SET cpu = ?, mem_percent = ?, mem_used = ?, mem_total = ?,
                        docker_mem = ?, cpu_queue_1m = ?,
                        disk_root_percent = ?, disk_mntc_percent = ?,
                        disk_read_bytes = ?, disk_write_bytes = ?,
                        net_rx_bytes = ?, net_tx_bytes = ?, n = ?
                    WHERE ts = ?
                    """,
                    (
                        (existing["cpu"] * n_old + row["cpu"] * n_new) / n_total,
                        (existing["mem_percent"] * n_old + row["mem_percent"] * n_new)
                        / n_total,
                        int(
                            (existing["mem_used"] * n_old + row["mem_used"] * n_new)
                            / n_total
                        ),
                        int(
                            (existing["mem_total"] * n_old + row["mem_total"] * n_new)
                            / n_total
                        ),
                        int(
                            (existing["docker_mem"] * n_old + row["docker_mem"] * n_new)
                            / n_total
                        ),
                        _avg(existing["cpu_queue_1m"], row["cpu_queue_1m"]) or 0.0,
                        _avg(existing["disk_root_percent"], row["disk_root_percent"]),
                        _avg(existing["disk_mntc_percent"], row["disk_mntc_percent"]),
                        int(
                            _avg(existing["disk_read_bytes"], row["disk_read_bytes"])
                            or 0
                        ),
                        int(
                            _avg(existing["disk_write_bytes"], row["disk_write_bytes"])
                            or 0
                        ),
                        int(_avg(existing["net_rx_bytes"], row["net_rx_bytes"]) or 0),
                        int(_avg(existing["net_tx_bytes"], row["net_tx_bytes"]) or 0),
                        n_total,
                        row["bucket_ts"],
                    ),
                )

    def latest_ts(self) -> int | None:
        row = self._conn.execute("SELECT MAX(ts) AS ts FROM samples").fetchone()
        return int(row["ts"]) if row and row["ts"] is not None else None

    def _row_to_sample(self, r: sqlite3.Row) -> SamplePoint:
        keys = set(r.keys())

        def _opt_float(name: str) -> float | None:
            if name not in keys:
                return None
            v = r[name]
            return None if v is None else float(v)

        def _int(name: str, default: int = 0) -> int:
            if name not in keys or r[name] is None:
                return default
            return int(r[name])

        return SamplePoint(
            ts=int(r["ts"]),
            cpu=float(r["cpu"]),
            mem_percent=float(r["mem_percent"]),
            mem_used=int(r["mem_used"]),
            mem_total=int(r["mem_total"]),
            docker_mem=int(r["docker_mem"]),
            cpu_queue_1m=float(r["cpu_queue_1m"]) if "cpu_queue_1m" in keys and r["cpu_queue_1m"] is not None else 0.0,
            disk_root_percent=_opt_float("disk_root_percent"),
            disk_mntc_percent=_opt_float("disk_mntc_percent"),
            disk_read_bytes=_int("disk_read_bytes"),
            disk_write_bytes=_int("disk_write_bytes"),
            net_rx_bytes=_int("net_rx_bytes"),
            net_tx_bytes=_int("net_tx_bytes"),
        )

    def query_samples(
        self,
        *,
        start: int,
        end: int,
        prefer: str = "auto",
    ) -> list[SamplePoint]:
        """Load samples for [start, end]. prefer: auto|raw|5m|1h."""
        span = end - start
        if prefer == "auto":
            if span <= RAW_RETENTION_S:
                prefer = "raw"
            elif span <= MED_RETENTION_S:
                prefer = "5m"
            else:
                prefer = "1h"

        table = {
            "raw": "samples",
            "5m": "samples_5m",
            "1h": "samples_1h",
        }.get(prefer, "samples")

        select_cols = (
            "ts, cpu, mem_percent, mem_used, mem_total, docker_mem, "
            "cpu_queue_1m, disk_root_percent, disk_mntc_percent, "
            "disk_read_bytes, disk_write_bytes, net_rx_bytes, net_tx_bytes"
        )
        rows = self._conn.execute(
            f"""
            SELECT {select_cols}
            FROM {table}
            WHERE ts BETWEEN ? AND ?
            ORDER BY ts ASC
            """,
            (start, end),
        ).fetchall()

        # Fall back to raw if downsampled table is empty for the window.
        if not rows and table != "samples":
            rows = self._conn.execute(
                f"""
                SELECT {select_cols}
                FROM samples
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts ASC
                """,
                (start, end),
            ).fetchall()

        return [self._row_to_sample(r) for r in rows]

    def query_processes(
        self, *, start: int, end: int
    ) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """
                SELECT ts, rank, name, pid, cpu, rss
                FROM top_processes
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts ASC, rank ASC
                """,
                (start, end),
            )
        )

    def query_containers(
        self, *, start: int, end: int
    ) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """
                SELECT ts, name, cpu, mem, status
                FROM containers
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts ASC, name ASC
                """,
                (start, end),
            )
        )

    def query_process_groups(
        self, *, start: int, end: int
    ) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """
                SELECT ts, name, count, cpu, rss
                FROM process_groups
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts ASC, name ASC
                """,
                (start, end),
            )
        )

    def _groups_at(self, ts: int) -> dict[str, tuple[int, int]]:
        """name -> (rss, count) at timestamp."""
        rows = self._conn.execute(
            """
            SELECT name, rss, count
            FROM process_groups
            WHERE ts = ?
            """,
            (ts,),
        ).fetchall()
        return {str(r["name"]): (int(r["rss"]), int(r["count"])) for r in rows}

    def previous_sample_ts(self, ts: int) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(ts) AS ts FROM samples WHERE ts < ?",
            (ts,),
        ).fetchone()
        return int(row["ts"]) if row and row["ts"] is not None else None

    def process_rss_deltas(self, *, top: int = 10) -> list[ProcessDelta]:
        """Top positive RSS deltas between the two latest samples (by name)."""
        latest = self.latest_ts()
        if latest is None:
            return []
        prev = self.previous_sample_ts(latest)
        if prev is None:
            return []
        after = self._groups_at(latest)
        before = self._groups_at(prev)
        deltas: list[ProcessDelta] = []
        names = set(after) | set(before)
        for name in names:
            rss_after, count_after = after.get(name, (0, 0))
            rss_before, _ = before.get(name, (0, 0))
            delta = rss_after - rss_before
            if delta <= 0:
                continue
            deltas.append(
                ProcessDelta(
                    name=name,
                    rss_before=rss_before,
                    rss_after=rss_after,
                    rss_delta=delta,
                    count_after=count_after,
                )
            )
        deltas.sort(key=lambda d: d.rss_delta, reverse=True)
        return deltas[: max(1, top)]

    def process_rss_growth(
        self, *, window_seconds: int = 3600, top: int = 10
    ) -> list[ProcessDelta]:
        """Top positive RSS growth from earliest sample in window to latest."""
        latest = self.latest_ts()
        if latest is None:
            return []
        start = latest - max(1, window_seconds)
        row = self._conn.execute(
            """
            SELECT MIN(ts) AS ts FROM samples
            WHERE ts BETWEEN ? AND ?
            """,
            (start, latest),
        ).fetchone()
        if not row or row["ts"] is None:
            return []
        first = int(row["ts"])
        if first == latest:
            return []
        after = self._groups_at(latest)
        before = self._groups_at(first)
        deltas: list[ProcessDelta] = []
        for name in set(after) | set(before):
            rss_after, count_after = after.get(name, (0, 0))
            rss_before, _ = before.get(name, (0, 0))
            delta = rss_after - rss_before
            if delta <= 0:
                continue
            deltas.append(
                ProcessDelta(
                    name=name,
                    rss_before=rss_before,
                    rss_after=rss_after,
                    rss_delta=delta,
                    count_after=count_after,
                )
            )
        deltas.sort(key=lambda d: d.rss_delta, reverse=True)
        return deltas[: max(1, top)]

    def _containers_at(self, ts: int) -> dict[str, int]:
        """name -> mem bytes at timestamp."""
        rows = self._conn.execute(
            """
            SELECT name, mem
            FROM containers
            WHERE ts = ?
            """,
            (ts,),
        ).fetchall()
        return {str(r["name"]): int(r["mem"]) for r in rows}

    def container_mem_deltas(self, *, top: int = 10) -> list[ContainerDelta]:
        """Top positive mem deltas between the two latest samples (by name)."""
        latest = self.latest_ts()
        if latest is None:
            return []
        prev = self.previous_sample_ts(latest)
        if prev is None:
            return []
        after = self._containers_at(latest)
        before = self._containers_at(prev)
        deltas: list[ContainerDelta] = []
        for name in set(after) | set(before):
            mem_after = after.get(name, 0)
            mem_before = before.get(name, 0)
            delta = mem_after - mem_before
            if delta <= 0:
                continue
            deltas.append(
                ContainerDelta(
                    name=name,
                    mem_before=mem_before,
                    mem_after=mem_after,
                    mem_delta=delta,
                )
            )
        deltas.sort(key=lambda d: d.mem_delta, reverse=True)
        return deltas[: max(1, top)]

    def container_mem_growth(
        self, *, window_seconds: int = 3600, top: int = 10
    ) -> list[ContainerDelta]:
        """Top positive container mem growth from earliest sample in window."""
        latest = self.latest_ts()
        if latest is None:
            return []
        start = latest - max(1, window_seconds)
        row = self._conn.execute(
            """
            SELECT MIN(ts) AS ts FROM samples
            WHERE ts BETWEEN ? AND ?
            """,
            (start, latest),
        ).fetchone()
        if not row or row["ts"] is None:
            return []
        first = int(row["ts"])
        if first == latest:
            return []
        after = self._containers_at(latest)
        before = self._containers_at(first)
        deltas: list[ContainerDelta] = []
        for name in set(after) | set(before):
            mem_after = after.get(name, 0)
            mem_before = before.get(name, 0)
            delta = mem_after - mem_before
            if delta <= 0:
                continue
            deltas.append(
                ContainerDelta(
                    name=name,
                    mem_before=mem_before,
                    mem_after=mem_after,
                    mem_delta=delta,
                )
            )
        deltas.sort(key=lambda d: d.mem_delta, reverse=True)
        return deltas[: max(1, top)]

    def stats_for(self, points: Iterable[SamplePoint]) -> dict[str, float]:
        pts = list(points)
        if not pts:
            return {"min_cpu": 0.0, "avg_cpu": 0.0, "max_cpu": 0.0,
                    "min_mem": 0.0, "avg_mem": 0.0, "max_mem": 0.0}
        cpus = [p.cpu for p in pts]
        mems = [p.mem_percent for p in pts]
        return {
            "min_cpu": min(cpus),
            "avg_cpu": sum(cpus) / len(cpus),
            "max_cpu": max(cpus),
            "min_mem": min(mems),
            "avg_mem": sum(mems) / len(mems),
            "max_mem": max(mems),
        }
