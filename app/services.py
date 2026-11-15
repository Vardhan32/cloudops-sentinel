"""HTTP service health checks with layered SSRF and abuse protections.

Threat model: checks run ONLY against explicitly configured ServiceTarget
rows (there is no endpoint that probes a user-supplied URL per request),
but a configured hostname may resolve to, or redirect to, an internal
destination. Defenses, applied on EVERY request (not just at add-time):

1. Strict URL syntax validation (scheme, host, credentials, port, length).
2. Host validation: literal IPs are checked directly; hostnames are
   resolved with ``socket.getaddrinfo`` and **every** returned address must
   be allowed — fail closed on any blocked address or resolution failure.
3. Explicit blocklist of non-public destinations (loopback unless
   explicitly allowed, RFC-1918, link-local incl. 169.254.169.254,
   multicast, reserved, CGNAT, documentation ranges), IPv4 and IPv6
   (including IPv4-mapped ``::ffff:a.b.c.d``).
4. Redirects are NOT auto-followed. Each hop (max 3) is re-validated
   through the same syntax + host checks; unsafe hops abort the check.
5. Bounded work: short timeout, connection-pool limits, streamed body
   capped at 256 KiB.

Loopback note: ``SENTINEL_ALLOW_LOOPBACK=1`` (default) permits 127.0.0.0/8
and ::1 so the bundled offline fixtures (``/api/test/ok|fail``) work with
no internet. Set it to ``0`` to block loopback too. This is documented in
``docs/SECURITY.md`` along with residual risks (notably DNS rebinding
between validation and connect, and the shared-resolver trust
assumption).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx

from . import config

ALLOWED_SCHEMES = ("http", "https")
MAX_URL_LENGTH = 1024
MAX_BODY_BYTES = 256 * 1024
MAX_REDIRECTS = 3
POOL_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)


def allow_loopback() -> bool:
    return os.getenv("SENTINEL_ALLOW_LOOPBACK", "1") == "1"


# Explicitly blocked networks. Checked with literal membership so behavior
# does not depend on ipaddress backport/version quirks.
_BLOCKED_V4 = [
    "0.0.0.0/8",  # software scope ("this host")
    "10.0.0.0/8",  # RFC-1918 private
    "100.64.0.0/10",  # CGNAT shared space
    "127.0.0.0/8",  # loopback (allowed only when allow_loopback())
    "169.254.0.0/16",  # link-local (incl. cloud metadata 169.254.169.254)
    "172.16.0.0/12",  # RFC-1918 private
    "192.0.0.0/24",  # IETF protocol assignments
    "192.0.2.0/24",  # TEST-NET-1 documentation
    "192.88.99.0/24",  # 6to4 relay (deprecated, anycast)
    "192.168.0.0/16",  # RFC-1918 private
    "198.18.0.0/15",  # benchmarking
    "198.51.100.0/24",  # TEST-NET-2 documentation
    "203.0.113.0/24",  # TEST-NET-3 documentation
    "224.0.0.0/4",  # multicast
    "240.0.0.0/4",  # reserved + broadcast 255.255.255.255
]
_BLOCKED_V6 = [
    "::/128",  # unspecified
    "::1/128",  # loopback (allowed only when allow_loopback())
    "64:ff9b::/96",  # IPv4/IPv6 translation
    "64:ff9b:1::/48",  # local-use translation
    "100::/64",  # discard
    "2001::/23",  # IETF special
    "2001:db8::/32",  # documentation (not covered by 2001::/23)
    "fc00::/7",  # unique-local (private)
    "fe80::/10",  # link-local
    "ff00::/8",  # multicast
]
_LOOPBACK_V4 = ipaddress.ip_network("127.0.0.0/8")
_LOOPBACK_V6 = ipaddress.ip_network("::1/128")


class SSRFError(ValueError):
    """Raised when a check destination is syntactically or policy invalid."""


def _blocked_networks() -> list[ipaddress._BaseNetwork]:
    nets = [ipaddress.ip_network(c) for c in _BLOCKED_V4 + _BLOCKED_V6]
    if allow_loopback():
        nets = [n for n in nets if n not in (_LOOPBACK_V4, _LOOPBACK_V6)]
    return nets


def is_ip_allowed(ip_text: str) -> bool:
    """True only for globally routable (or explicitly allowed loopback) IPs."""
    try:
        ip = ipaddress.ip_address(ip_text.split("%")[0])
    except ValueError:
        return False
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) and judge the inner IP.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not any(ip in net for net in _blocked_networks())


def _resolve_host(hostname: str, port: int) -> list[str]:
    """Resolve a hostname to candidate IP strings. Raises SSRFError."""
    if "%" in hostname:
        raise SSRFError("Zone identifiers in hostnames are not allowed.")
    try:
        ascii_host = hostname.encode("idna").decode("ascii")
    except Exception as exc:
        raise SSRFError(f"Invalid hostname encoding: {exc}") from exc
    try:
        infos = socket.getaddrinfo(ascii_host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFError(f"Hostname does not resolve: {hostname}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise SSRFError(f"Hostname does not resolve: {hostname}")
    return addresses


def validate_host(hostname: str, port: int) -> str:
    """Resolve + policy-check a host. Returns primary IP or raises SSRFError."""
    if not hostname:
        raise SSRFError("URL must include a hostname.")
    candidate = hostname.strip("[]")
    try:
        ipaddress.ip_address(candidate.split("%")[0])
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False
    if is_literal_ip:
        # Literal IP (v4 or v6): no DNS involved.
        if is_ip_allowed(candidate):
            return candidate
        raise SSRFError(f"Destination IP is not allowed: {candidate}")
    addresses = _resolve_host(candidate, port)
    blocked = [a for a in addresses if not is_ip_allowed(a)]
    if blocked:
        raise SSRFError(
            f"Hostname resolves to prohibited address(es): {', '.join(blocked)}"
        )
    return addresses[0]


def _split_url(url: str) -> tuple[str, str, int]:
    """Return (scheme, hostname, port) or raise SSRFError."""
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise SSRFError(f"Malformed URL: {exc}") from exc
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFError(
            f"Only http:// and https:// URLs are allowed "
            f"(got scheme '{parsed.scheme}')."
        )
    if not parsed.hostname:
        raise SSRFError("URL must include a hostname.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise SSRFError(f"Invalid URL port: {exc}") from exc
    if not (1 <= port <= 65535):
        raise SSRFError("URL port must be between 1 and 65535.")
    return parsed.scheme, parsed.hostname, port


def validate_service_url(url: str) -> str:
    """Validate a candidate target URL (syntax + resolvable safe host).

    Returns the trimmed URL or raises ValueError (SSRFError subclasses it,
    so existing callers are unaffected).
    """
    if not isinstance(url, str):
        raise ValueError("URL must be a string.")
    url = url.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise ValueError("URL must be 1-1024 characters.")
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ValueError("URL must not contain whitespace or control characters.")
    _split_url(url)  # raises if scheme/host/port are invalid
    authority = url.split("://", 1)[1].split("/", 1)[0]
    if "@" in authority:
        raise ValueError("URLs with embedded credentials are not allowed.")
    try:
        _, hostname, port = _split_url(url)
        validate_host(hostname, port)
    except SSRFError as exc:
        raise ValueError(str(exc)) from exc
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


class GuardedTransport(httpx.HTTPTransport):
    """httpx transport that re-validates the destination per request.

    Catches DNS-rebinding drift between add-time validation and connect
    time: the host is resolved and policy-checked immediately before the
    socket is opened. Redirects are never auto-followed (the caller walks
    hops explicitly through :func:`check_service`).
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _, hostname, port = _split_url(str(request.url))
        validate_host(hostname, port)  # fail closed
        return super().handle_request(request)


def _single_get(
    client: httpx.Client, url: str
) -> tuple[int, float, httpx.Headers, bytes]:
    """GET with bounded body. Returns (status, elapsed_ms, headers, body)."""
    started = time.perf_counter()
    with client.stream("GET", url) as response:
        headers = response.headers
        status = response.status_code
        chunks: list[bytes] = []
        remaining = MAX_BODY_BYTES
        for chunk in response.iter_bytes(65536):
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            remaining -= len(chunk)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return status, round(elapsed_ms, 2), headers, b"".join(chunks)


def check_service(url: str, timeout: float | None = None) -> dict:
    """Perform one guarded HTTP GET health check. Never raises.

    Returns dict(status_code, response_time_ms, healthy, error).
    """
    timeout = config.SERVICE_CHECK_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        _, hostname, port = _split_url(url)
        validate_host(hostname, port)
    except SSRFError as exc:
        return {
            "status_code": None,
            "response_time_ms": None,
            "healthy": False,
            "error": f"Blocked destination: {exc}",
        }
    try:
        transport = GuardedTransport(retries=0)
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            limits=POOL_LIMITS,
            follow_redirects=False,
        ) as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                status, elapsed_ms, headers, _body = _single_get(client, current)
                if status in (301, 302, 303, 307, 308):
                    location = headers.get("location", "")
                    if not location:
                        return {
                            "status_code": status,
                            "response_time_ms": elapsed_ms,
                            "healthy": False,
                            "error": f"Redirect without Location (status {status})",
                        }
                    nxt = urljoin(current, location)
                    try:
                        _, h, p = _split_url(nxt)
                        validate_host(h, p)
                    except SSRFError as exc:
                        return {
                            "status_code": status,
                            "response_time_ms": elapsed_ms,
                            "healthy": False,
                            "error": f"Blocked redirect destination: {exc}",
                        }
                    current = nxt
                    continue
                healthy = 200 <= status < 300
                return {
                    "status_code": status,
                    "response_time_ms": elapsed_ms,
                    "healthy": healthy,
                    "error": "" if healthy else f"Unexpected status {status}",
                }
            return {
                "status_code": None,
                "response_time_ms": None,
                "healthy": False,
                "error": f"Too many redirects (>{MAX_REDIRECTS})",
            }
    except SSRFError as exc:
        return {
            "status_code": None,
            "response_time_ms": None,
            "healthy": False,
            "error": f"Blocked destination: {exc}",
        }
    except httpx.TimeoutException:
        return {
            "status_code": None,
            "response_time_ms": None,
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
