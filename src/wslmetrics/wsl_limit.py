from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

_MEMORY_RE = re.compile(
    r"^\s*memory\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B?)?\s*$",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def parse_memory_value(raw: str) -> int | None:
    """Parse values like 8GB, 8192MB, 8G into bytes."""
    text = raw.strip().strip('"').strip("'")
    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B?)?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    if unit in {"", "B"}:
        mult = 1
    elif unit in {"K", "KB"}:
        mult = 1024
    elif unit in {"M", "MB"}:
        mult = 1024**2
    elif unit in {"G", "GB"}:
        mult = 1024**3
    elif unit in {"T", "TB"}:
        mult = 1024**4
    else:
        return None
    return int(amount * mult)


def parse_wslconfig_memory(text: str) -> int | None:
    section: str | None = None
    for line in text.splitlines():
        stripped = line.split(";", 1)[0].split("#", 1)[0].strip()
        if not stripped:
            continue
        sec = _SECTION_RE.match(stripped)
        if sec:
            section = sec.group(1).strip().lower()
            continue
        if section != "wsl2":
            continue
        mem = _MEMORY_RE.match(stripped)
        if mem:
            unit = mem.group(2) or ""
            return parse_memory_value(f"{mem.group(1)}{unit}")
    return None


def _env_userprofile_path() -> Path | None:
    """Map USERPROFILE / Windows-style env vars into /mnt/<drive>/... if present."""
    for key in ("USERPROFILE", "WIN_USERPROFILE"):
        env_profile = os.environ.get(key)
        if not env_profile or len(env_profile) < 3:
            continue
        if env_profile[1] == ":":
            drive = env_profile[0].lower()
            rest = env_profile[2:].replace("\\", "/").lstrip("/")
            candidate = Path(f"/mnt/{drive}") / rest
        else:
            candidate = Path(env_profile)
        if candidate.is_dir():
            return candidate
    return None


def _candidate_wslconfig_paths() -> list[Path]:
    """Locate .wslconfig under /mnt/c/Users without calling Windows binaries."""
    paths: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        if path not in seen:
            seen.add(path)
            paths.append(path)

    profile = _env_userprofile_path()
    if profile is not None:
        add(profile / ".wslconfig")

    users = Path("/mnt/c/Users")
    if users.is_dir():
        try:
            children = sorted(users.iterdir())
        except OSError:
            children = []
        for child in children:
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            name = child.name.lower()
            if name in {"public", "default", "default user", "all users"}:
                continue
            add(child / ".wslconfig")
    return paths


@lru_cache(maxsize=1)
def get_wsl_memory_limit() -> int | None:
    """Return WSL2 memory= limit from .wslconfig in bytes, or None."""
    for path in _candidate_wslconfig_paths():
        try:
            if not path.is_file():
                continue
            limit = parse_wslconfig_memory(
                path.read_text(encoding="utf-8", errors="ignore")
            )
            if limit and limit > 0:
                return limit
        except OSError:
            continue
    return None


def wsl_usage_percent(mem_used: int, limit: int | None = None) -> float | None:
    lim = limit if limit is not None else get_wsl_memory_limit()
    if not lim:
        return None
    return (mem_used / lim) * 100.0
