from wslmetrics.formatting import format_bytes, format_percent


def test_format_bytes_units() -> None:
    assert format_bytes(512) == "512B"
    assert format_bytes(1024) == "1.0KiB"
    assert format_bytes(1024**2) == "1.0MiB"
    assert format_bytes(1024**3) == "1.0GiB"


def test_format_percent() -> None:
    assert format_percent(12.345) == "12.3%"
    assert format_percent(100, digits=0) == "100%"
