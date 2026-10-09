"""Health endpoint and API validation tests."""


def test_health_ok(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["app"] == "CloudOps Sentinel"
    assert body["database"] == "ok"
    assert "version" in body


def test_incident_filter_validation(client):
    res = client.get("/api/incidents", params={"status": "bogus"})
    assert res.status_code == 422


def test_incident_not_found(client):
    assert client.get("/api/incidents/999999").status_code == 404
    assert client.post("/api/incidents/999999/acknowledge").status_code == 404
    assert client.post("/api/incidents/999999/resolve").status_code == 404


def test_openapi_available(client):
    assert client.get("/openapi.json").status_code == 200
