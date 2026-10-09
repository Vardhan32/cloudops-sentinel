"""Real local system metrics collection via psutil.

Only genuine readings are returned as "observed". Anything else in the
API is explicitly labelled "simulated".
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import psutil


def collect_system_metrics() -> dict[str, Any]:
    """Collect CPU, memory, disk, and uptime from the local host.

    Raises RuntimeError if psutil cannot provide readings.
    """
    try:
        cpu = float(psutil.cpu_percent(interval=0.2))
        memory = float(psutil.virtual_memory().percent)
        disk_path = os.path.abspath(os.sep)
        disk = float(psutil.disk_usage(disk_path).percent)
        boot = psutil.boot_time()
        uptime = max(0.0, time.time() - float(boot))
    except Exception as exc:  # pragma: no cover - environment failure
        raise RuntimeError(f"Failed to collect system metrics: {exc}") from exc
    return {
        "cpu_percent": cpu,
        "memory_percent": memory,
        "disk_percent": disk,
        "uptime_seconds": uptime,
        "timestamp": datetime.now(timezone.utc),
        "source": "observed",
    }
