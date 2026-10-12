from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SortKey(str, Enum):
    CPU = "cpu"
    MEM = "mem"


@dataclass(frozen=True, slots=True)
class ProcessRow:
    pid: int
    name: str
    cpu_percent: float
    mem_percent: float
    rss_bytes: int
    username: str


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


@dataclass(frozen=True, slots=True)
class Snapshot:
    summary: SystemSummary
    processes: tuple[ProcessRow, ...] = field(default_factory=tuple)
    grouped_processes: tuple[GroupedProcessRow, ...] = field(default_factory=tuple)
    containers: tuple[ContainerRow, ...] = field(default_factory=tuple)
