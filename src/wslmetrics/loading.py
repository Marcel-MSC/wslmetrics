from __future__ import annotations

import time
from collections.abc import Sequence

from textual.timer import Timer
from textual.widgets import Static

LOADING_INTERVAL = 0.12
LOADING_MIN_SECONDS = 2.0

# Shared stage labels + filled bar cells (width 8).
_LOADING_STAGES: tuple[tuple[str, int], ...] = (
    ("Getting", 0),
    ("Collecting", 1),
    ("Gathering", 2),
    ("Retrieving", 3),
    ("Enriching", 4),
    ("Persisting", 5),
    ("Almoust", 6),
    ("Nearly", 7),
    ("Ahaaaa", 8),
)


def loading_bar(filled: int, *, width: int = 8) -> str:
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def summary_loading_frames(
    stages: Sequence[tuple[str, int]] = _LOADING_STAGES,
) -> tuple[str, ...]:
    """One-line frames for the top SummaryBar."""
    return tuple(
        f"◈ {label:<11} [{loading_bar(filled)}] reading cells…"
        for label, filled in stages
    )


def panel_loading_frames(
    stages: Sequence[tuple[str, int]] = _LOADING_STAGES,
) -> tuple[str, ...]:
    """Boxed frames for the centered RamLoader panes."""
    frames: list[str] = []
    for label, filled in stages:
        bar = loading_bar(filled)
        # Keep box width aligned with ┌─[########]─┐ (14 chars inner content).
        mid = f"◈ {label}".center(12)[:12]
        frames.append(
            f"┌─[{bar}]─┐\n"
            f"│{mid}│\n"
            f"└────────────┘"
        )
    return tuple(frames)


SUMMARY_FRAMES = summary_loading_frames()
PANEL_FRAMES = panel_loading_frames()


class LoadingAnimator:
    """Shared frame ticker + minimum display duration gate."""

    def __init__(
        self,
        frames: Sequence[str],
        *,
        min_seconds: float = LOADING_MIN_SECONDS,
    ) -> None:
        self.frames = tuple(frames) or ("…",)
        self.min_seconds = max(0.0, min_seconds)
        self.frame = 0
        self.started = 0.0
        self.active = False

    @property
    def current(self) -> str:
        return self.frames[self.frame]

    def start(self) -> str:
        self.active = True
        self.frame = 0
        self.started = time.monotonic()
        return self.current

    def tick(self) -> str:
        self.frame = (self.frame + 1) % len(self.frames)
        return self.current

    def min_elapsed(self) -> bool:
        if not self.active:
            return True
        return (time.monotonic() - self.started) >= self.min_seconds

    def should_finish(self, ready: bool) -> bool:
        return self.active and ready and self.min_elapsed()

    def stop(self) -> None:
        self.active = False


class RamLoader(Static):
    """Centered animated loading indicator (shared stages with SummaryBar)."""

    DEFAULT_CSS = """
    RamLoader {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
        text-style: bold;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(PANEL_FRAMES[0], id=id)
        self._frame = 0
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(LOADING_INTERVAL, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(PANEL_FRAMES)
        self.update(PANEL_FRAMES[self._frame])
