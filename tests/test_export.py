from __future__ import annotations

import json
from pathlib import Path

from wslmetrics.export import export_csv, export_json
from wslmetrics.models import (
    ContainerRow,
    DiskIO,
    DiskMount,
    GroupedProcessRow,
    NetIO,
    ProcessRow,
    SystemSummary,
)
from wslmetrics.prometheus import render_prom_text, write_prom_textfile
from wslmetrics.storage import MetricsStore


def _summary(**kwargs) -> SystemSummary:
    base = dict(
        mem_total=8_000_000_000,
        mem_used=4_000_000_000,
        mem_available=4_000_000_000,
        mem_percent=50.0,
        cpu_percent=25.0,
        docker_mem_total=10,
        cpu_queue_1m=1.25,
        disk_mounts=(
            DiskMount("/", 1000, 400, 600, 40.0),
            DiskMount("/mnt/c", 2000, 500, 1500, 25.0),
        ),
        disk_io=DiskIO(read_bytes=11, write_bytes=22),
        net_io=NetIO(iface="eth0", rx_bytes=33, tx_bytes=44),
    )
    base.update(kwargs)
    return SystemSummary(**base)


def test_export_csv_and_json_sidecars(
    store_path: Path, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("wslmetrics.export.MetricsStore", lambda: MetricsStore(store_path))
    procs = (
        ProcessRow(1, "chrome", 1.0, 1.0, 100, "u"),
        ProcessRow(2, "node", 2.0, 2.0, 200, "u"),
    )
    groups = (GroupedProcessRow("chrome", 1, 1.0, 1.0, 100, "u"),)
    containers = (
        ContainerRow("api", "a1", 1.0, 50, 100, 50.0, "running", "api:1"),
    )
    with MetricsStore(store_path) as store:
        store.insert_sample(
            _summary(), procs, containers, groups=groups, ts=1_700_000_000
        )

    csv_out = tmp_path / "metrics.csv"
    export_csv(
        csv_out,
        from_ts="1700000000",
        to_ts="1700000100",
        include_processes=True,
        include_containers=True,
        include_groups=True,
    )
    text = csv_out.read_text(encoding="utf-8")
    assert "cpu_queue_1m" in text
    assert "disk_root_percent" in text
    assert "1.25" in text
    assert (tmp_path / "metrics_processes.csv").exists()
    assert (tmp_path / "metrics_containers.csv").exists()
    assert (tmp_path / "metrics_groups.csv").exists()
    assert "api" in (tmp_path / "metrics_containers.csv").read_text(encoding="utf-8")
    assert "chrome" in (tmp_path / "metrics_groups.csv").read_text(encoding="utf-8")

    json_out = tmp_path / "metrics.json"
    export_json(
        json_out,
        from_ts="1700000000",
        to_ts="1700000100",
        include_processes=True,
        include_containers=True,
        include_groups=True,
    )
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert len(data["samples"]) == 1
    assert data["samples"][0]["cpu_queue_1m"] == 1.25
    assert data["samples"][0]["disk_root_percent"] == 40.0
    assert data["containers"][0]["name"] == "api"
    assert data["groups"][0]["name"] == "chrome"
    assert data["processes"][0]["pid"] == 1


def test_prom_textfile(tmp_path: Path) -> None:
    summary = _summary()
    text = render_prom_text(summary)
    assert "wslmetrics_cpu_percent 25.0000" in text
    assert "wslmetrics_cpu_queue_1m 1.2500" in text
    assert 'wslmetrics_disk_used_percent{path="/"} 40.0000' in text
    assert 'wslmetrics_net_rx_bytes_total{iface="eth0"} 33' in text
    # HELP/TYPE once per metric family
    assert text.count("# TYPE wslmetrics_disk_used_percent gauge") == 1

    path = write_prom_textfile(summary, path=tmp_path / "metrics.prom")
    assert path.exists()
    assert "wslmetrics_mem_percent" in path.read_text(encoding="utf-8")
