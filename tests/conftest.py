from __future__ import annotations

from pathlib import Path

import pytest

from wslmetrics.models import (
    ContainerRow,
    GroupedProcessRow,
    ProcessRow,
    SystemSummary,
)


@pytest.fixture
def summary() -> SystemSummary:
    return SystemSummary(
        mem_total=8 * 1024**3,
        mem_used=4 * 1024**3,
        mem_available=4 * 1024**3,
        mem_percent=50.0,
        cpu_percent=20.0,
        docker_mem_total=0,
    )


@pytest.fixture
def sample_processes() -> tuple[ProcessRow, ...]:
    return (
        ProcessRow(
            pid=1,
            name="chrome",
            cpu_percent=10.0,
            mem_percent=5.0,
            rss_bytes=100_000_000,
            username="user",
        ),
        ProcessRow(
            pid=2,
            name="chrome",
            cpu_percent=5.0,
            mem_percent=3.0,
            rss_bytes=50_000_000,
            username="user",
        ),
        ProcessRow(
            pid=3,
            name="node",
            cpu_percent=30.0,
            mem_percent=8.0,
            rss_bytes=200_000_000,
            username="user",
        ),
    )


@pytest.fixture
def sample_groups() -> tuple[GroupedProcessRow, ...]:
    return (
        GroupedProcessRow(
            name="chrome",
            count=2,
            cpu_percent=15.0,
            mem_percent=10.0,
            rss_bytes=150_000_000,
            username="user",
        ),
        GroupedProcessRow(
            name="node",
            count=1,
            cpu_percent=30.0,
            mem_percent=8.0,
            rss_bytes=200_000_000,
            username="user",
        ),
    )


@pytest.fixture
def running_container() -> ContainerRow:
    return ContainerRow(
        name="api",
        short_id="abc123",
        cpu_percent=12.5,
        mem_usage_bytes=256_000_000,
        mem_limit_bytes=512_000_000,
        mem_percent=50.0,
        status="running",
        image="api:latest",
    )


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics.db"
