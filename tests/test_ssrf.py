"""SSRF regression tests. No real network use: DNS is mocked, and no test
connects to internal infrastructure — prohibited destinations are rejected
before any socket is opened."""

import socket
from unittest.mock import patch

import httpx
import pytest

from app import services
from app.services import (
    MAX_BODY_BYTES,
    check_service,
    is_ip_allowed,
    validate_host,
    validate_service_url,
)


@pytest.mark.parametrize(
    "ip,allowed",
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("9.9.9.9", True),
        ("10.0.0.1", False),
        ("172.16.5.4", False),
        ("172.31.255.255", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("169.254.10.20", False),
        ("0.0.0.0", False),
        ("255.255.255.255", False),
        ("224.0.0.1", False),
        ("100.64.0.1", False),  # CGNAT
        ("192.0.2.1", False),  # documentation
        ("198.51.100.7", False),
        ("203.0.113.9", False),
        ("198.18.0.1", False),
        ("::", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("ff02::1", False),
        ("2001:db8::1", False),
        ("::ffff:10.0.0.1", False),  # IPv4-mapped private
        ("::ffff:169.254.169.254", False),
        ("not-an-ip", False),
    ],
)
def test_ip_blocklist(ip, allowed):
    assert is_ip_allowed(ip) is allowed


def test_loopback_allowed_by_default():
    assert is_ip_allowed("127.0.0.1") is True
    assert is_ip_allowed("::1") is True
    assert is_ip_allowed("::ffff:127.0.0.1") is True


def test_loopback_blocked_when_disabled(monkeypatch):
    monkeypatch.setenv("SENTINEL_ALLOW_LOOPBACK", "0")
    assert is_ip_allowed("127.0.0.1") is False
    assert is_ip_allowed("::1") is False
    # Public IPs unaffected.
    assert is_ip_allowed("8.8.8.8") is True


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/health",
        "http://172.16.0.1:8080/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://[::]/",
        "http://[fe80::1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://user:pass@127.0.0.1/",
        "http://127.0.0.1:99999/",
        "ftp://127.0.0.1/file",
        "file:///etc/passwd",
    ],
)
def test_add_time_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        validate_service_url(url)


def test_add_time_accepts_loopback_fixture():
    assert (
        validate_service_url("http://127.0.0.1:8000/api/test/ok")
        == "http://127.0.0.1:8000/api/test/ok"
    )


def _addr(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]


def test_hostname_resolving_to_private_rejected():
    with patch.object(socket, "getaddrinfo", return_value=_addr("10.1.2.3")):
        with pytest.raises(ValueError, match="prohibited"):
            validate_service_url("http://internal.example/health")


def test_hostname_resolving_to_public_accepted():
    with patch.object(socket, "getaddrinfo", return_value=_addr("93.184.216.34")):
        assert (
            validate_service_url("http://example.example/health")
            == "http://example.example/health"
        )


def test_unresolvable_hostname_rejected():
    with patch.object(
        socket, "getaddrinfo", side_effect=socket.gaierror("no such host")
    ):
        with pytest.raises(ValueError, match="does not resolve"):
            validate_service_url("http://nonexistent.invalid/")


def test_mixed_dns_results_fail_closed():
    addrs = _addr("93.184.216.34") + _addr("192.168.0.5")
    with patch.object(socket, "getaddrinfo", return_value=addrs):
        with pytest.raises(ValueError, match="prohibited"):
            validate_host("mixed.example", 80)


def test_check_service_blocks_private_without_connecting():
    with patch.object(socket, "getaddrinfo") as resolve:
        result = check_service("http://10.0.0.1/health")
    resolve.assert_not_called()  # literal IP: no DNS needed
    assert result["healthy"] is False
    assert "Blocked destination" in result["error"]
    assert result["status_code"] is None


def test_check_service_blocks_evil_hostname():
    with patch.object(socket, "getaddrinfo", return_value=_addr("169.254.169.254")):
        with patch("app.services.httpx.Client") as client_cls:
            result = check_service("http://metadata.example/")
    client_cls.assert_not_called()  # rejected before any socket opened
    assert result["healthy"] is False
    assert "Blocked destination" in result["error"]


def test_redirect_to_private_aborted():
    redirect = (301, 1.0, httpx.Headers({"location": "http://10.9.9.9/"}), b"")
    with patch("app.services._single_get", return_value=redirect):
        result = check_service("http://127.0.0.1:8000/start")
    assert result["healthy"] is False
    assert "Blocked redirect" in result["error"]


def test_redirect_chain_too_long():
    hop = (302, 1.0, httpx.Headers({"location": "http://127.0.0.1:8000/next"}), b"")
    with patch("app.services._single_get", return_value=hop):
        result = check_service("http://127.0.0.1:8000/start")
    assert result["healthy"] is False
    assert "Too many redirects" in result["error"]


def test_healthy_redirect_followed_with_reevaluation():
    first = (302, 2.0, httpx.Headers({"location": "/api/test/ok"}), b"")
    second = (200, 3.0, httpx.Headers({}), b'{"status":"ok"}')
    with patch("app.services._single_get", side_effect=[first, second]):
        result = check_service("http://127.0.0.1:8000/start")
    assert result["healthy"] is True
    assert result["status_code"] == 200


def test_response_body_capped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024))

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        status, _, _, body = services._single_get(client, "http://test.local/")
    assert status == 200
    assert len(body) <= MAX_BODY_BYTES
