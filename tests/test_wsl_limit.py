from unittest.mock import patch

from wslmetrics.wsl_limit import parse_memory_value, parse_wslconfig_memory, wsl_usage_percent


def test_parse_memory_value() -> None:
    assert parse_memory_value("8GB") == 8 * 1024**3
    assert parse_memory_value("8192MB") == 8192 * 1024**2
    assert parse_memory_value("512") == 512
    assert parse_memory_value("not-a-size") is None


def test_parse_wslconfig_memory() -> None:
    text = """
[wsl2]
processors=4
memory=16GB
swap=2GB
"""
    assert parse_wslconfig_memory(text) == 16 * 1024**3


def test_parse_wslconfig_ignores_other_sections() -> None:
    text = """
[user]
memory=99GB
[wsl2]
memory=4G
"""
    assert parse_wslconfig_memory(text) == 4 * 1024**3


def test_wsl_usage_percent() -> None:
    limit = 8 * 1024**3
    used = 4 * 1024**3
    assert wsl_usage_percent(used, limit) == 50.0
    with patch("wslmetrics.wsl_limit.get_wsl_memory_limit", return_value=None):
        assert wsl_usage_percent(used, None) is None
