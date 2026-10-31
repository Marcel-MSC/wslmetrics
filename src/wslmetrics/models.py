from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SortKey(str, Enum):
    CPU = "cpu"
    MEM = "mem"


@dataclass(frozen=True, slots=True)
class DiskMount:
    path: str
    total: int
    used: int
    free: int
    percent: float


@dataclass(frozen=True, slots=True)
class DiskIO:
    read_bytes: int
    write_bytes: int


@dataclass(frozen=True, slots=True)
class NetIO:
    iface: str
    rx_bytes: int
    tx_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessRow:
    pid: int
    name: str
    cpu_percent: float
    mem_percent: float
    rss_bytes: int
    username: str
    ppid: int = 0
    cmdline: str = ""


@dataclass(frozen=True, slots=True)
class GroupedProcessRow:
    name: str
    count: int
    cpu_percent: float
    mem_percent: float
    rss_bytes: int
    username: str


@dataclass(frozen=True, slots=True)
class ProcessDelta:
    name: str
    rss_before: int
    rss_after: int
    rss_delta: int
    count_after: int


@dataclass(frozen=True, slots=True)
class ContainerDelta:
    name: str
    mem_before: int
    mem_after: int
    mem_delta: int


@dataclass(frozen=True, slots=True)
class ContainerRow:
    name: str
    short_id: str
    cpu_percent: float
    mem_usage_bytes: int
    mem_limit_bytes: int
    mem_percent: float
    status: str
    image: str


@dataclass(frozen=True, slots=True)
class ProcessDetail:
    pid: int
    name: str
    username: str
    cpu_percent: float
    mem_percent: float
    rss_bytes: int
    ppid: int
    parent_name: str
    cmdline: str
    children: tuple[tuple[int, str], ...]  # (pid, name)


@dataclass(frozen=True, slots=True)
class SystemSummary:
    mem_total: int
    mem_used: int
    mem_available: int
    mem_percent: float
    cpu_percent: float
    docker_mem_total: int = 0
    docker_available: bool = True
    docker_error: str | None = None
    wsl_mem_limit: int | None = None
    wsl_mem_percent: float | None = None
    swap_total: int = 0
    swap_used: int = 0
    swap_percent: float = 0.0
    wsl_swap_limit: int | None = None
    wsl_swap_percent: float | None = None
    cpu_queue_1m: float = 0.0
    cpu_queue_5m: float = 0.0
    cpu_queue_15m: float = 0.0
    disk_mounts: tuple[DiskMount, ...] = ()
    disk_io: DiskIO | None = None
    net_io: NetIO | None = None
    cpu_per_core: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class Snapshot:
    summary: SystemSummary
    processes: tuple[ProcessRow, ...] = field(default_factory=tuple)
    grouped_processes: tuple[GroupedProcessRow, ...] = field(default_factory=tuple)
    containers: tuple[ContainerRow, ...] = field(default_factory=tuple)
