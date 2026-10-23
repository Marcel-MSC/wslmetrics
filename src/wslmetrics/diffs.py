from __future__ import annotations

from wslmetrics.formatting import format_bytes
from wslmetrics.models import ContainerDelta, ProcessDelta
from wslmetrics.storage import MetricsStore

_GLOSSARY = """\
Dictionary
  RSS  Resident Set Size — RAM the process is using right now (physical pages)
  N    quantity of processes with that name
  dRSS RSS growth in the window (after − before)
  dMEM container working-set memory growth (after − before)
"""


def _process_section(title: str, deltas: list[ProcessDelta]) -> list[str]:
    lines = [title]
    if not deltas:
        lines.append("  (no positive RSS growth yet — need more samples)")
        lines.append("")
        return lines

    total = sum(d.rss_delta for d in deltas)
    lines.append(
        f"  {'NAME':<22} {'dRSS':>10} {'BEFORE':>10} {'AFTER':>10} {'N':>4}"
    )
    lines.append("  " + "-" * 60)
    for d in deltas:
        lines.append(
            f"  {d.name[:22]:<22} {format_bytes(d.rss_delta):>10} "
            f"{format_bytes(d.rss_before):>10} {format_bytes(d.rss_after):>10} "
            f"{d.count_after:>4}"
        )
    lines.append(f"  top gains sum: {format_bytes(total)}")
    lines.append("")
    return lines


def _container_section(title: str, deltas: list[ContainerDelta]) -> list[str]:
    lines = [title]
    if not deltas:
        lines.append("  (no positive container mem growth yet — need more samples)")
        lines.append("")
        return lines

    total = sum(d.mem_delta for d in deltas)
    lines.append(
        f"  {'NAME':<22} {'dMEM':>10} {'BEFORE':>10} {'AFTER':>10}"
    )
    lines.append("  " + "-" * 54)
    for d in deltas:
        lines.append(
            f"  {d.name[:22]:<22} {format_bytes(d.mem_delta):>10} "
            f"{format_bytes(d.mem_before):>10} {format_bytes(d.mem_after):>10}"
        )
    lines.append(f"  top gains sum: {format_bytes(total)}")
    lines.append("")
    return lines


def render_diffs_text(*, top: int = 10) -> str:
    with MetricsStore() as store:
        last = store.process_rss_deltas(top=top)
        hour = store.process_rss_growth(window_seconds=3600, top=top)
        c_last = store.container_mem_deltas(top=top)
        c_hour = store.container_mem_growth(window_seconds=3600, top=top)

    lines: list[str] = [_GLOSSARY.rstrip(), ""]
    lines.extend(_process_section("Since last sample — who grew RAM?", last))
    lines.extend(
        _process_section("Last 1h growth — breakdown by process name", hour)
    )
    lines.extend(
        _container_section("Containers — since last sample", c_last)
    )
    lines.extend(
        _container_section("Containers — last 1h growth", c_hour)
    )
    return "\n".join(lines).rstrip() + "\n"
