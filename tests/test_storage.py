from __future__ import annotations

from pathlib import Path

from wslmetrics.models import GroupedProcessRow, SystemSummary
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
