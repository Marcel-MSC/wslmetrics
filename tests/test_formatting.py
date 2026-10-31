from wslmetrics.formatting import format_bytes, format_percent, format_rate
from wslmetrics.rates import counter_rate, summary_io_rates
from wslmetrics.models import DiskIO, NetIO, SystemSummary


def test_format_bytes_units() -> None:
    assert format_bytes(512) == "512B"
    assert format_bytes(1024) == "1.0KiB"
    assert format_bytes(1024**2) == "1.0MiB"
    assert format_bytes(1024**3) == "1.0GiB"


def test_format_percent() -> None:
    assert format_percent(12.345) == "12.3%"
    assert format_percent(100, digits=0) == "100%"


def test_format_rate() -> None:
    assert format_rate(None) == "—"
    assert format_rate(1024) == "1.0KiB/s"


def test_counter_rate() -> None:
    assert counter_rate(None, 100, 1.0) is None
    assert counter_rate(100, 300, 2.0) == 100.0
    assert counter_rate(300, 100, 1.0) is None  # reset


def test_summary_io_rates() -> None:
    base = dict(
        mem_total=1,
        mem_used=1,
        mem_available=0,
        mem_percent=100.0,
        cpu_percent=1.0,
    )
    prev = SystemSummary(
        **base,
        disk_io=DiskIO(100, 200),
        net_io=NetIO("eth0", 1000, 2000),
    )
    curr = SystemSummary(
        **base,
        disk_io=DiskIO(300, 600),
        net_io=NetIO("eth0", 1500, 2600),
    )
    rates = summary_io_rates(prev, curr, 2.0)
    assert rates["disk_read"] == 100.0
    assert rates["disk_write"] == 200.0
    assert rates["net_rx"] == 250.0
    assert rates["net_tx"] == 300.0
