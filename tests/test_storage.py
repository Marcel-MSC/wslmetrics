from __future__ import annotations

from pathlib import Path

from wslmetrics.models import ContainerRow, GroupedProcessRow, SystemSummary
from wslmetrics.storage import (
    BUCKET_5M,
    MED_RETENTION_S,
    RAW_RETENTION_S,
    MetricsStore,
)


def _summary(**kwargs) -> SystemSummary:
    base = dict(
        mem_total=8_000_000_000,
        mem_used=4_000_000_000,
        mem_available=4_000_000_000,
        mem_percent=50.0,
        cpu_percent=25.0,
        docker_mem_total=0,
    )
    base.update(kwargs)
    return SystemSummary(**base)


def test_insert_and_query(store_path: Path, sample_processes, sample_groups, running_container) -> None:
    with MetricsStore(store_path) as store:
        ts = store.insert_sample(
            _summary(),
            sample_processes,
            (running_container,),
            groups=sample_groups,
            ts=1_700_000_000,
        )
        assert ts == 1_700_000_000
        points = store.query_samples(start=ts, end=ts, prefer="raw")
        assert len(points) == 1
        assert points[0].cpu == 25.0
        assert store.latest_ts() == ts


def test_downsample_raw_to_5m(store_path: Path) -> None:
    now = 2_000_000_000
    old = now - RAW_RETENTION_S - 60
    with MetricsStore(store_path) as store:
        store.insert_sample(_summary(cpu_percent=10.0), (), (), ts=old)
        store.insert_sample(_summary(cpu_percent=30.0), (), (), ts=now)
        store.maintain(now=now)

        left = store._conn.execute(
            "SELECT COUNT(*) AS c FROM samples WHERE ts = ?", (old,)
        ).fetchone()["c"]
        assert left == 0
        bucket = (old // BUCKET_5M) * BUCKET_5M
        row = store._conn.execute(
            "SELECT cpu FROM samples_5m WHERE ts = ?", (bucket,)
        ).fetchone()
        assert row is not None
        assert abs(row["cpu"] - 10.0) < 0.01
        # recent raw kept
        assert store.latest_ts() == now


def test_downsample_5m_to_1h(store_path: Path) -> None:
    now = 3_000_000_000
    old_5m = now - MED_RETENTION_S - 60
    with MetricsStore(store_path) as store:
        # Seed an old 5m bucket directly
        store._conn.execute(
            """
            INSERT INTO samples_5m
                (ts, cpu, mem_percent, mem_used, mem_total, docker_mem, n)
            VALUES (?, 40.0, 50.0, 1, 2, 0, 3)
            """,
            (old_5m,),
        )
        store._conn.commit()
        store.maintain(now=now)
        left = store._conn.execute(
            "SELECT COUNT(*) AS c FROM samples_5m WHERE ts = ?", (old_5m,)
        ).fetchone()["c"]
        assert left == 0
        hour_bucket = (old_5m // 3600) * 3600
        row = store._conn.execute(
            "SELECT cpu, n FROM samples_1h WHERE ts = ?", (hour_bucket,)
        ).fetchone()
        assert row is not None
        assert abs(row["cpu"] - 40.0) < 0.01


def test_process_rss_deltas_and_growth(store_path: Path) -> None:
    t0 = 1_800_000_000
    t1 = t0 + 30
    g0 = (
        GroupedProcessRow("chrome", 2, 1.0, 1.0, 100, "u"),
        GroupedProcessRow("node", 1, 1.0, 1.0, 50, "u"),
    )
    g1 = (
        GroupedProcessRow("chrome", 3, 1.0, 1.0, 180, "u"),
        GroupedProcessRow("node", 1, 1.0, 1.0, 40, "u"),
    )
    with MetricsStore(store_path) as store:
        store.insert_sample(_summary(), (), (), groups=g0, ts=t0)
        store.insert_sample(_summary(), (), (), groups=g1, ts=t1)
        deltas = store.process_rss_deltas(top=10)
        assert deltas[0].name == "chrome"
        assert deltas[0].rss_delta == 80
        assert all(d.rss_delta > 0 for d in deltas)

        growth = store.process_rss_growth(window_seconds=3600, top=10)
        assert growth[0].name == "chrome"
        assert growth[0].rss_before == 100
        assert growth[0].rss_after == 180


def test_insert_persists_disk_load_net(store_path: Path) -> None:
    from wslmetrics.models import DiskIO, DiskMount, NetIO

    summary = _summary(
        cpu_queue_1m=2.5,
        disk_mounts=(
            DiskMount("/", 100, 40, 60, 40.0),
            DiskMount("/mnt/c", 200, 50, 150, 25.0),
        ),
        disk_io=DiskIO(read_bytes=10, write_bytes=20),
        net_io=NetIO(iface="eth0", rx_bytes=30, tx_bytes=40),
    )
    with MetricsStore(store_path) as store:
        store.insert_sample(summary, (), (), ts=1_710_000_000)
        points = store.query_samples(
            start=1_710_000_000, end=1_710_000_000, prefer="raw"
        )
        assert len(points) == 1
        p = points[0]
        assert p.cpu_queue_1m == 2.5
        assert p.disk_root_percent == 40.0
        assert p.disk_mntc_percent == 25.0
        assert p.disk_read_bytes == 10
        assert p.net_rx_bytes == 30


def test_migrate_renames_load1_to_cpu_queue_1m(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "rename.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE samples (
            ts INTEGER PRIMARY KEY,
            cpu REAL NOT NULL,
            mem_percent REAL NOT NULL,
            mem_used INTEGER NOT NULL,
            mem_total INTEGER NOT NULL,
            docker_mem INTEGER NOT NULL DEFAULT 0,
            load1 REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE samples_5m (
            ts INTEGER PRIMARY KEY,
            cpu REAL NOT NULL,
            mem_percent REAL NOT NULL,
            mem_used INTEGER NOT NULL,
            mem_total INTEGER NOT NULL,
            docker_mem INTEGER NOT NULL DEFAULT 0,
            n INTEGER NOT NULL DEFAULT 1,
            load1 REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE samples_1h (
            ts INTEGER PRIMARY KEY,
            cpu REAL NOT NULL,
            mem_percent REAL NOT NULL,
            mem_used INTEGER NOT NULL,
            mem_total INTEGER NOT NULL,
            docker_mem INTEGER NOT NULL DEFAULT 0,
            n INTEGER NOT NULL DEFAULT 1,
            load1 REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE top_processes (
            ts INTEGER NOT NULL, rank INTEGER NOT NULL, name TEXT NOT NULL,
            pid INTEGER NOT NULL, cpu REAL NOT NULL, rss INTEGER NOT NULL,
            PRIMARY KEY (ts, rank)
        );
        CREATE TABLE containers (
            ts INTEGER NOT NULL, name TEXT NOT NULL, cpu REAL NOT NULL,
            mem INTEGER NOT NULL, status TEXT NOT NULL, PRIMARY KEY (ts, name)
        );
        CREATE TABLE process_groups (
            ts INTEGER NOT NULL, name TEXT NOT NULL, count INTEGER NOT NULL,
            cpu REAL NOT NULL, rss INTEGER NOT NULL, PRIMARY KEY (ts, name)
        );
        INSERT INTO samples (ts, cpu, mem_percent, mem_used, mem_total, docker_mem, load1)
        VALUES (42, 1.0, 2.0, 3, 4, 0, 1.75);
        """
    )
    conn.commit()
    conn.close()

    with MetricsStore(path) as store:
        cols = {
            r["name"]
            for r in store._conn.execute("PRAGMA table_info(samples)").fetchall()
        }
        assert "cpu_queue_1m" in cols
        assert "load1" not in cols
        points = store.query_samples(start=42, end=42, prefer="raw")
        assert points[0].cpu_queue_1m == 1.75


def test_migrate_adds_extra_columns_on_old_db(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE samples (
            ts INTEGER PRIMARY KEY,
            cpu REAL NOT NULL,
            mem_percent REAL NOT NULL,
            mem_used INTEGER NOT NULL,
            mem_total INTEGER NOT NULL,
            docker_mem INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE samples_5m (
            ts INTEGER PRIMARY KEY,
            cpu REAL NOT NULL,
            mem_percent REAL NOT NULL,
            mem_used INTEGER NOT NULL,
            mem_total INTEGER NOT NULL,
            docker_mem INTEGER NOT NULL DEFAULT 0,
            n INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE samples_1h (
            ts INTEGER PRIMARY KEY,
            cpu REAL NOT NULL,
            mem_percent REAL NOT NULL,
            mem_used INTEGER NOT NULL,
            mem_total INTEGER NOT NULL,
            docker_mem INTEGER NOT NULL DEFAULT 0,
            n INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE top_processes (
            ts INTEGER NOT NULL, rank INTEGER NOT NULL, name TEXT NOT NULL,
            pid INTEGER NOT NULL, cpu REAL NOT NULL, rss INTEGER NOT NULL,
            PRIMARY KEY (ts, rank)
        );
        CREATE TABLE containers (
            ts INTEGER NOT NULL, name TEXT NOT NULL, cpu REAL NOT NULL,
            mem INTEGER NOT NULL, status TEXT NOT NULL, PRIMARY KEY (ts, name)
        );
        CREATE TABLE process_groups (
            ts INTEGER NOT NULL, name TEXT NOT NULL, count INTEGER NOT NULL,
            cpu REAL NOT NULL, rss INTEGER NOT NULL, PRIMARY KEY (ts, name)
        );
        """
    )
    conn.commit()
    conn.close()

    with MetricsStore(path) as store:
        cols = {
            r["name"]
            for r in store._conn.execute("PRAGMA table_info(samples)").fetchall()
        }
        assert "cpu_queue_1m" in cols
        assert "disk_root_percent" in cols
        assert "net_tx_bytes" in cols
        store.insert_sample(_summary(cpu_queue_1m=3.0), (), (), ts=1)
        points = store.query_samples(start=1, end=1, prefer="raw")
        assert points[0].cpu_queue_1m == 3.0


def test_container_mem_deltas_and_growth(store_path: Path) -> None:
    t0 = 1_900_000_000
    t1 = t0 + 30
    c0 = (
        ContainerRow(
            "api", "a1", 1.0, 100_000_000, 512_000_000, 20.0, "running", "api:1"
        ),
        ContainerRow(
            "db", "b1", 1.0, 200_000_000, 512_000_000, 40.0, "running", "db:1"
        ),
    )
    c1 = (
        ContainerRow(
            "api", "a1", 1.0, 180_000_000, 512_000_000, 35.0, "running", "api:1"
        ),
        ContainerRow(
            "db", "b1", 1.0, 190_000_000, 512_000_000, 37.0, "running", "db:1"
        ),
    )
    with MetricsStore(store_path) as store:
        store.insert_sample(_summary(), (), c0, ts=t0)
        store.insert_sample(_summary(), (), c1, ts=t1)
        deltas = store.container_mem_deltas(top=10)
        assert deltas[0].name == "api"
        assert deltas[0].mem_delta == 80_000_000
        assert all(d.mem_delta > 0 for d in deltas)

        growth = store.container_mem_growth(window_seconds=3600, top=10)
        assert growth[0].name == "api"
        assert growth[0].mem_before == 100_000_000
        assert growth[0].mem_after == 180_000_000
