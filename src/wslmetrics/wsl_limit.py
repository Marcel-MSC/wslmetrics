from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_KEY_VALUE_RE = re.compile(
    r"^\s*(memory|swap)\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B?)?\s*$",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


@dataclass(frozen=True, slots=True)
class WslLimits:
    memory: int | None = None
    swap: int | None = None


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


def parse_wslconfig_limits(text: str) -> WslLimits:
    """Parse memory= and swap= from the [wsl2] section."""
    section: str | None = None
    memory: int | None = None
    swap: int | None = None
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
        kv = _KEY_VALUE_RE.match(stripped)
        if not kv:
            continue
        key = kv.group(1).lower()
        unit = kv.group(3) or ""
        value = parse_memory_value(f"{kv.group(2)}{unit}")
        if value is None or value <= 0:
            continue
        if key == "memory" and memory is None:
            memory = value
        elif key == "swap" and swap is None:
            swap = value
    return WslLimits(memory=memory, swap=swap)


def parse_wslconfig_memory(text: str) -> int | None:
    return parse_wslconfig_limits(text).memory


def parse_wslconfig_swap(text: str) -> int | None:
    return parse_wslconfig_limits(text).swap


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
def get_wsl_limits() -> WslLimits:
    """Return WSL2 memory=/swap= limits from .wslconfig, or empty limits."""
    for path in _candidate_wslconfig_paths():
        try:
            if not path.is_file():
                continue
            limits = parse_wslconfig_limits(
                path.read_text(encoding="utf-8", errors="ignore")
            )
            if limits.memory or limits.swap:
                return limits
        except OSError:
            continue
    return WslLimits()


@lru_cache(maxsize=1)
def get_wsl_memory_limit() -> int | None:
    """Return WSL2 memory= limit from .wslconfig in bytes, or None."""
    return get_wsl_limits().memory


@lru_cache(maxsize=1)
def get_wsl_swap_limit() -> int | None:
    """Return WSL2 swap= limit from .wslconfig in bytes, or None."""
    return get_wsl_limits().swap


def wsl_usage_percent(used: int, limit: int | None = None) -> float | None:
    lim = limit if limit is not None else get_wsl_memory_limit()
    if not lim:
        return None
    return (used / lim) * 100.0
