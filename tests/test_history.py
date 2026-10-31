from __future__ import annotations

from wslmetrics.history import _section
from wslmetrics.storage import SamplePoint


def _points(*docker_mems: int) -> list[SamplePoint]:
    return [
        SamplePoint(
            ts=1_700_000_000 + i,
            cpu=10.0 + i,
            mem_percent=40.0 + i,
            mem_used=1,
            mem_total=2,
            docker_mem=dm,
        )
        for i, dm in enumerate(docker_mems)
    ]


def test_history_section_includes_docker_mem_title() -> None:
    lines = _section(
        _points(100_000_000, 200_000_000, 150_000_000),
        title="1h",
        metric="docker_mem",
        width=48,
        height=6,
    )
    joined = "\n".join(lines)
    assert "Docker mem" in joined
    assert "no docker_mem data" not in joined


def test_history_section_empty_docker_mem() -> None:
    lines = _section(
        _points(0, 0, 0),
        title="1h",
        metric="docker_mem",
        width=48,
        height=6,
    )
    assert "(no docker_mem data)" in "\n".join(lines)


def test_history_section_load_and_disk() -> None:
    points = [
        SamplePoint(
            ts=1,
            cpu=1.0,
            mem_percent=10.0,
            mem_used=1,
            mem_total=2,
            docker_mem=0,
            cpu_queue_1m=0.5,
            disk_root_percent=40.0,
            disk_mntc_percent=25.0,
        ),
        SamplePoint(
            ts=2,
            cpu=2.0,
            mem_percent=20.0,
            mem_used=1,
            mem_total=2,
            docker_mem=0,
            cpu_queue_1m=1.5,
            disk_root_percent=45.0,
            disk_mntc_percent=30.0,
        ),
    ]
    load = "\n".join(
        _section(points, title="1h", metric="cpu_queue_1m", width=48, height=6)
    )
    assert "CPU queue (1m)" in load
    disk = "\n".join(
        _section(points, title="1h", metric="disk_root", width=48, height=6)
    )
    assert "Disk / %" in disk
    mntc = "\n".join(
        _section(points, title="1h", metric="disk_mntc", width=48, height=6)
    )
    assert "Disk /mnt/c %" in mntc
