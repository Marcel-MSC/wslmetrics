from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from wslmetrics.models import (
    ContainerRow,
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


@dataclass(frozen=True, slots=True)
class SamplePoint:
    ts: int
    cpu: float
    mem_percent: float
    mem_used: int
    mem_total: int
    docker_mem: int


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
        self._conn.commit()

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
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO samples
                    (ts, cpu, mem_percent, mem_used, mem_total, docker_mem)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    float(summary.cpu_percent),
                    float(summary.mem_percent),
                    int(summary.mem_used),
                    int(summary.mem_total),
                    int(summary.docker_mem_total),
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
                COUNT(*) AS n
            FROM {source}
            WHERE ts < ?
            GROUP BY bucket_ts
            """,
            (bucket, bucket, older_than),
        ).fetchall()
        for row in rows:
            existing = self._conn.execute(
                f"SELECT n, cpu, mem_percent, mem_used, mem_total, docker_mem "
                f"FROM {dest} WHERE ts = ?",
                (row["bucket_ts"],),
            ).fetchone()
            if existing is None:
                self._conn.execute(
                    f"""
                    INSERT INTO {dest}
                        (ts, cpu, mem_percent, mem_used, mem_total, docker_mem, n)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["bucket_ts"],
                        row["cpu"],
                        row["mem_percent"],
                        int(row["mem_used"]),
                        int(row["mem_total"]),
                        int(row["docker_mem"]),
                        int(row["n"]),
                    ),
                )
            else:
                n_old = int(existing["n"])
                n_new = int(row["n"])
                n_total = n_old + n_new
                self._conn.execute(
                    f"""
                    UPDATE {dest}
                    SET cpu = ?, mem_percent = ?, mem_used = ?, mem_total = ?,
                        docker_mem = ?, n = ?
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
                        n_total,
                        row["bucket_ts"],
                    ),
                )

    def latest_ts(self) -> int | None:
        row = self._conn.execute("SELECT MAX(ts) AS ts FROM samples").fetchone()
        return int(row["ts"]) if row and row["ts"] is not None else None

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

        rows = self._conn.execute(
            f"""
            SELECT ts, cpu, mem_percent, mem_used, mem_total, docker_mem
            FROM {table}
            WHERE ts BETWEEN ? AND ?
            ORDER BY ts ASC
            """,
            (start, end),
        ).fetchall()

        # Fall back to raw if downsampled table is empty for the window.
        if not rows and table != "samples":
            rows = self._conn.execute(
                """
                SELECT ts, cpu, mem_percent, mem_used, mem_total, docker_mem
                FROM samples
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts ASC
                """,
                (start, end),
            ).fetchall()

        return [
            SamplePoint(
                ts=int(r["ts"]),
                cpu=float(r["cpu"]),
                mem_percent=float(r["mem_percent"]),
                mem_used=int(r["mem_used"]),
                mem_total=int(r["mem_total"]),
                docker_mem=int(r["docker_mem"]),
            )
            for r in rows
        ]

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
