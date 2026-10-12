from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from wslmetrics.collectors.host import HostCollector
from wslmetrics.models import SortKey


def _fake_proc(pid: int, name: str, rss: int, cpu: float, user: str = "u") -> MagicMock:
    proc = MagicMock()
    proc.info = {
        "pid": pid,
        "name": name,
        "username": user,
        "memory_info": SimpleNamespace(rss=rss),
        "memory_percent": (rss / (8 * 1024**3)) * 100,
    }
    proc.cpu_percent.return_value = cpu
    return proc


@patch("wslmetrics.collectors.host.psutil.virtual_memory")
@patch("wslmetrics.collectors.host.psutil.cpu_percent", return_value=0.0)
@patch("wslmetrics.collectors.host.psutil.process_iter")
def test_processes_and_grouped(
    mock_iter: MagicMock,
    _cpu: MagicMock,
    mock_vm: MagicMock,
) -> None:
    mock_vm.return_value = SimpleNamespace(
        total=8 * 1024**3, used=1, available=1, percent=1.0
    )
    procs = [
        _fake_proc(1, "chrome", 100_000_000, 10.0),
        _fake_proc(2, "chrome", 50_000_000, 5.0),
        _fake_proc(3, "node", 200_000_000, 40.0),
    ]
    mock_iter.return_value = procs

    host = HostCollector(prime_processes=False)
    by_cpu = host.processes(top=10, sort_key=SortKey.CPU)
    assert by_cpu[0].name == "node"
    assert len(by_cpu) == 3

    grouped = host.grouped_processes(
        top=10, sort_key=SortKey.MEM, mem_total=8 * 1024**3
    )
    chrome = next(g for g in grouped if g.name == "chrome")
    assert chrome.count == 2
    assert chrome.rss_bytes == 150_000_000
    assert chrome.cpu_percent == 15.0
    assert grouped[0].name == "node"  # highest rss
