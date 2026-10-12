from __future__ import annotations

from textual.timer import Timer
from textual.widgets import Static


def _build_frames() -> tuple[str, ...]:
    """Build DIMM-style fill frames without hardcoding messy unicode blobs."""
    width = 8
    labels = (
        "reading",
        "reading",
        "reading",
        "scanning",
        "scanning",
        "loading",
        "loading",
        "loading",
        "almost",
        "reading",
        "reading",
        "reading",
    )
    frames: list[str] = []
    # Fill left-to-right, then slide a window for a "scan" feel.
    pattern = list(range(0, width + 1)) + list(range(width - 1, 0, -1))
    for i, filled in enumerate(pattern):
        if i < width + 1:
            bar = "▓" * filled + "░" * (width - filled)
        else:
            # sliding block of 5 cells
            start = (i - (width + 1)) % (width - 2)
            bar_chars = ["░"] * width
            for j in range(start, min(start + 5, width)):
                bar_chars[j] = "▓"
            bar = "".join(bar_chars)
        label = labels[i % len(labels)]
        # Match top border width: ─[########]─  => 12 inner chars between corners.
        inner = f"── {label} ".ljust(12, "─")[:12]
        frames.append(
            f"┌─[{bar}]─┐\n"
            f"│   ◈ RAM    │\n"
            f"└{inner}┘"
        )
    return tuple(frames)


_FRAMES = _build_frames()


class RamLoader(Static):
    """Centered animated RAM-chip loading indicator."""

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
        super().__init__(_FRAMES[0], id=id)
        self._frame = 0
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.12, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        self._frame = (self._frame + 1) % len(_FRAMES)
        self.update(_FRAMES[self._frame])
