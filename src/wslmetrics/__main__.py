from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wslmetrics.app import MonitorApp
from wslmetrics.export import export_csv, export_json
from wslmetrics.jobs import install_job, status_report, uninstall_job
from wslmetrics.sample import format_sample_result, run_sample


def _add_export_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--from",
        dest="from_ts",
        default=None,
        help="Start time (epoch seconds or ISO-8601). Default: 24h ago",
    )
    parser.add_argument(
        "--to",
        dest="to_ts",
        default=None,
        help="End time (epoch seconds or ISO-8601). Default: now",
    )
    parser.add_argument(
        "--include-processes",
        action="store_true",
        help="Also include top processes (raw 24h window)",
    )
    parser.add_argument(
        "--include-containers",
        action="store_true",
        help="Also include Docker containers (raw 24h window)",
    )
    parser.add_argument(
        "--include-groups",
        action="store_true",
        help="Also include process groups (raw 24h window)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wslmetrics",
        description="Monitor host and Docker CPU/RAM usage in a live WSL TUI.",
    )
    sub = parser.add_subparsers(dest="command")

    tui = sub.add_parser("tui", help="Open the live TUI (default)")
    tui.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Refresh interval in seconds (default: 1.0)",
    )
    tui.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of top processes to show (default: 30)",
    )

    # Also accept legacy top-level flags when no subcommand is used.
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )

    sub.add_parser("sample", help="Collect one sample into SQLite and evaluate alerts")

    export_p = sub.add_parser("export-csv", help="Export metrics history to CSV")
    export_p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("metrics.csv"),
        help="Output CSV path (default: metrics.csv)",
    )
    _add_export_flags(export_p)

    export_j = sub.add_parser("export-json", help="Export metrics history to JSON")
    export_j.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("metrics.json"),
        help="Output JSON path (default: metrics.json)",
    )
    _add_export_flags(export_j)

    sub.add_parser("install-job", help="Install and enable systemd --user sample timer")
    sub.add_parser("uninstall-job", help="Disable and remove systemd --user sample timer")
    sub.add_parser("status", help="Show timer, last sample, and recent alerts")

    return parser


def _run_tui(interval: float, top: int) -> None:
    app = MonitorApp(interval=interval, top=top)
    app.run()


def _print_sidecar_paths(
    path: Path,
    *,
    include_processes: bool,
    include_containers: bool,
    include_groups: bool,
) -> None:
    if include_processes:
        print(f"Wrote {path.with_name(path.stem + '_processes' + path.suffix)}")
    if include_containers:
        print(f"Wrote {path.with_name(path.stem + '_containers' + path.suffix)}")
    if include_groups:
        print(f"Wrote {path.with_name(path.stem + '_groups' + path.suffix)}")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # Default to TUI when no subcommand is given (keeps `wslmetrics` UX).
    known = {
        "tui",
        "sample",
        "export-csv",
        "export-json",
        "install-job",
        "uninstall-job",
        "status",
        "-h",
        "--help",
    }
    if not argv or (argv[0].startswith("-") and argv[0] not in known):
        argv = ["tui", *argv]

    args = parser.parse_args(argv)
    command = args.command or "tui"

    if command == "tui":
        interval = (
            args.interval
            if getattr(args, "interval", None) is not None
            else 1.0
        )
        # Prefer subcommand values; fall back to suppressed top-level if present.
        top = args.top if getattr(args, "top", None) is not None else 30
        _run_tui(float(interval), int(top))
        return

    if command == "sample":
        result = run_sample()
        print(format_sample_result(result))
        return

    if command == "export-csv":
        path = export_csv(
            args.output,
            from_ts=args.from_ts,
            to_ts=args.to_ts,
            include_processes=args.include_processes,
            include_containers=args.include_containers,
            include_groups=args.include_groups,
        )
        print(f"Wrote {path}")
        _print_sidecar_paths(
            path,
            include_processes=args.include_processes,
            include_containers=args.include_containers,
            include_groups=args.include_groups,
        )
        return

    if command == "export-json":
        path = export_json(
            args.output,
            from_ts=args.from_ts,
            to_ts=args.to_ts,
            include_processes=args.include_processes,
            include_containers=args.include_containers,
            include_groups=args.include_groups,
        )
        print(f"Wrote {path}")
        return

    if command == "install-job":
        try:
            print(install_job())
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            raise SystemExit(1) from exc
        return

    if command == "uninstall-job":
        print(uninstall_job())
        return

    if command == "status":
        print(status_report())
        return

    parser.error(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
