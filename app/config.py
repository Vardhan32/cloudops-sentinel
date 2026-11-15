"""Central configuration for CloudOps Sentinel.

All thresholds and service targets are configurable here or via
environment variables. Nothing here requires cloud access.
"""

from __future__ import annotations

import os


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


APP_NAME = "CloudOps Sentinel"
APP_VERSION = "1.0.0"

# Warning / critical thresholds (percent).
CPU_WARN = _float_env("SENTINEL_CPU_WARN", 75.0)
CPU_CRIT = _float_env("SENTINEL_CPU_CRIT", 90.0)
MEM_WARN = _float_env("SENTINEL_MEM_WARN", 80.0)
MEM_CRIT = _float_env("SENTINEL_MEM_CRIT", 90.0)
# Disk usage percent (100 = full). "Low disk space" == high usage.
DISK_WARN = _float_env("SENTINEL_DISK_WARN", 80.0)
DISK_CRIT = _float_env("SENTINEL_DISK_CRIT", 90.0)

# Background monitor interval in seconds.
MONITOR_INTERVAL_SECONDS = _int_env("SENTINEL_MONITOR_INTERVAL", 30)
# HTTP service check timeout in seconds.
SERVICE_CHECK_TIMEOUT_SECONDS = _float_env("SENTINEL_SERVICE_TIMEOUT", 5.0)

# SQLite database location. Overridden in tests via DATABASE_URL.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/sentinel.db")


def validate_config() -> None:
    """Fail fast on nonsensical configuration. Raises ValueError."""
    pairs = [
        ("CPU", CPU_WARN, CPU_CRIT),
        ("MEM", MEM_WARN, MEM_CRIT),
        ("DISK", DISK_WARN, DISK_CRIT),
    ]
    for label, warn, crit in pairs:
        if not (0 < warn < crit <= 100):
            raise ValueError(
                f"Invalid {label} thresholds: require 0 < warn < crit <= 100 "
                f"(got warn={warn}, crit={crit})."
            )
    if MONITOR_INTERVAL_SECONDS < 5:
        raise ValueError("SENTINEL_MONITOR_INTERVAL must be >= 5 seconds.")
    if SERVICE_CHECK_TIMEOUT_SECONDS <= 0:
        raise ValueError("SENTINEL_SERVICE_TIMEOUT must be > 0.")
