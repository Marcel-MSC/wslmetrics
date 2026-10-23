from __future__ import annotations

from pathlib import Path

from wslmetrics.alerts_view import has_hot_alert, render_alerts_text, tail_alert_log
from wslmetrics.config import AlertConfig, AppConfig


def _cfg(**kwargs) -> AppConfig:
    alerts = dict(
        mem_percent=90.0,
        cpu_percent=95.0,
        docker_mem_bytes=None,
        wsl_mem_percent=90.0,
        swap_percent=None,
        cooldown_seconds=300,
    )
    alerts.update(kwargs)
    return AppConfig(
        alerts=AlertConfig(**alerts),
        sample_interval_seconds=30,
    )


def test_tail_alert_log(tmp_path: Path) -> None:
    log = tmp_path / "alerts.log"
    log.write_text("a\nb\nc\nd\n", encoding="utf-8")
    assert tail_alert_log(log_path=log, limit=2) == ["c", "d"]
    assert tail_alert_log(log_path=tmp_path / "missing.log") == []


def test_render_alerts_text_and_hot(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    log = tmp_path / "alerts.log"
    state.write_text(
        '{"last_alert": {"mem_percent": 1000.0, "cpu_percent": 0}}',
        encoding="utf-8",
    )
    log.write_text("2026-01-01T00:00:00Z RAM high\n", encoding="utf-8")
    cfg = _cfg(cooldown_seconds=100)
    text = render_alerts_text(
        cfg=cfg, state_path=state, log_path=log, now=1050.0, log_limit=10
    )
    assert "mem_percent" in text
    assert "swap_percent" in text
    assert "RAM high" in text
    assert "ready" in text or "50s" in text
    assert has_hot_alert(cfg=cfg, state_path=state, now=1050.0) is True
    assert has_hot_alert(cfg=cfg, state_path=state, now=2000.0) is False
