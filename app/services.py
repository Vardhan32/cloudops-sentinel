"""HTTP service health checks with SSRF and abuse protections.

Safety model:
- Checks run ONLY against explicitly configured ServiceTarget rows.
  There is no endpoint that accepts an arbitrary URL to probe.
- Adding a target validates the URL strictly: http/https only, a real
  hostname, no embedded credentials, no whitespace/control characters,
  valid port range. Dangerous schemes (file://, gopher://, ftp://, ...)
  are rejected.
- Every request uses a short timeout so a hung endpoint cannot stall
  the monitor.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from . import config

ALLOWED_SCHEMES = ("http", "https")
MAX_URL_LENGTH = 1024


def validate_service_url(url: str) -> str:
    """Validate a candidate service URL. Returns normalized URL or raises ValueError."""
    if not isinstance(url, str):
        raise ValueError("URL must be a string.")
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValueError("URL must be 1-1024 characters.")
    if any(ch.isspace() for ch in url):
        raise ValueError("URL must not contain whitespace.")
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise ValueError(f"Malformed URL: {exc}") from exc
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            f"Only http:// and https:// URLs are allowed (got scheme '{parsed.scheme}')."
        )
    if not parsed.hostname:
        raise ValueError("URL must include a hostname.")
    if parsed.username or parsed.password or "@" in url.split("://", 1)[1].split("/", 1)[0]:
        raise ValueError("URLs with embedded credentials are not allowed.")
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise ValueError("URL port must be between 1 and 65535.")
    return url


def validate_service_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Name must be a string.")
    name = name.strip()
    if not name or len(name) > 128:
        raise ValueError("Name must be 1-128 characters.")
    if any(ord(ch) < 32 for ch in name):
        raise ValueError("Name must not contain control characters.")
    return name


def check_service(url: str, timeout: float | None = None) -> dict:
    """Perform one HTTP GET health check. Never raises; failures are data."""
    timeout = config.SERVICE_CHECK_TIMEOUT_SECONDS if timeout is None else timeout
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        healthy = 200 <= response.status_code < 400
        return {
            "status_code": response.status_code,
            "response_time_ms": round(elapsed_ms, 2),
            "healthy": healthy,
            "error": "" if healthy else f"Unexpected status {response.status_code}",
        }
    except httpx.TimeoutException:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "status_code": None,
            "response_time_ms": round(elapsed_ms, 2),
            "healthy": False,
            "error": f"Request timed out after {timeout}s",
        }
    except httpx.ConnectError as exc:
        return {
            "status_code": None,
            "response_time_ms": None,
            "healthy": False,
            "error": f"Connection failed: {exc.__class__.__name__}",
        }
    except httpx.HTTPError as exc:
        return {
            "status_code": None,
            "response_time_ms": None,
            "healthy": False,
            "error": f"HTTP error: {exc.__class__.__name__}",
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "status_code": None,
            "response_time_ms": None,
            "healthy": False,
            "error": f"Check failed: {exc.__class__.__name__}",
        }
