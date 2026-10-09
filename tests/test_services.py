"""Service checks: validation, timeout/connection handling, persistence."""

from unittest.mock import patch

import httpx
import pytest

from app.services import check_service, validate_service_url


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://example.com/health",
        "file:///etc/passwd",
        "gopher://example.com/",
        "javascript:alert(1)",
        "http://user:pass@example.com/",
        "http://",
        "notaurl",
        "http://exam ple.com/",
        "",
    ],
)
def test_url_validation_rejects_dangerous(bad):
    with pytest.raises(ValueError):
        validate_service_url(bad)


def test_url_validation_accepts_http():
    assert validate_service_url("http://127.0.0.1:8000/api/test/ok").startswith("http://")


def test_check_timeout_handled():
    def boom(*a, **k):
        raise httpx.TimeoutException("timed out")

    with patch("app.services.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.side_effect = boom
        result = check_service("http://127.0.0.1:9/health", timeout=0.1)
    assert result["healthy"] is False
    assert "timed out" in result["error"].lower()


def test_check_connection_error_handled():
    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    with patch("app.services.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.side_effect = boom
        result = check_service("http://127.0.0.1:9/health")
    assert result["healthy"] is False
    assert result["status_code"] is None


def test_service_crud_and_check_api(client):
    # Invalid URL rejected.
    bad = client.post("/api/services", json={"name": "evil", "url": "file:///etc/passwd"})
    assert bad.status_code == 422

    good = client.post(
        "/api/services", json={"name": "local-ok", "url": "http://127.0.0.1:8000/api/test/ok"}
    )
    assert good.status_code == 201
    service_id = good.json()["id"]

    # Duplicate name rejected.
    dup = client.post(
        "/api/services", json={"name": "local-ok", "url": "http://127.0.0.1:8000/api/test/ok"}
    )
    assert dup.status_code == 409

    # Mock the actual HTTP call so no network is needed.
    with patch("app.main.check_service") as mock_check:
        mock_check.return_value = {
            "status_code": 200,
            "response_time_ms": 3.0,
            "healthy": True,
            "error": "",
        }
        res = client.post(f"/api/services/{service_id}/check")
    assert res.status_code == 200
    assert res.json()["healthy"] is True

    history = client.get("/api/services/history", params={"service_id": service_id}).json()
    assert len(history) == 1

    assert client.delete(f"/api/services/{service_id}").status_code == 204


def test_failing_check_creates_incident_and_recovery_resolves(client):
    created = client.post(
        "/api/services", json={"name": "flaky", "url": "http://127.0.0.1:9/missing"}
    )
    service_id = created.json()["id"]

    with patch("app.main.check_service") as mock_check:
        mock_check.return_value = {
            "status_code": None,
            "response_time_ms": None,
            "healthy": False,
            "error": "Connection failed: ConnectError",
        }
        client.post(f"/api/services/{service_id}/check")
        client.post(f"/api/services/{service_id}/check")  # second failure: still one incident

    open_incidents = client.get("/api/incidents", params={"status": "open"}).json()
    service_incidents = [i for i in open_incidents if i["dedup_key"] == "service:flaky"]
    assert len(service_incidents) == 1

    with patch("app.main.check_service") as mock_check:
        mock_check.return_value = {
            "status_code": 200,
            "response_time_ms": 2.0,
            "healthy": True,
            "error": "",
        }
        client.post(f"/api/services/{service_id}/check")

    open_incidents = client.get("/api/incidents", params={"status": "open"}).json()
    assert all(i["dedup_key"] != "service:flaky" for i in open_incidents)
