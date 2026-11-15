"""API extras: boundaries, empty DB, invalid input, DB failure,
dashboard integration, simulated/observed separation, monitor cycle."""

from datetime import datetime, timezone
from unittest.mock import patch

from app import monitor
from app.main import run_monitor_cycle
from app.models import Incident, MetricSnapshot, ServiceTarget


def _reading(cpu=10.0, mem=20.0, disk=30.0):
    return {
        "cpu_percent": cpu,
        "memory_percent": mem,
        "disk_percent": disk,
        "uptime_seconds": 60.0,
        "timestamp": datetime.now(timezone.utc),
        "source": "observed",
    }


def test_threshold_boundaries():
    assert monitor.evaluate_thresholds(74.9, 20.0, 30.0) == []
    warn = monitor.evaluate_thresholds(75.0, 20.0, 30.0)
    assert len(warn) == 1 and warn[0]["severity"] == "warning"
    crit = monitor.evaluate_thresholds(90.0, 20.0, 30.0)
    assert crit[0]["severity"] == "critical" and crit[0]["threshold"] == 90.0


def test_empty_history_and_summary(client):
    assert client.get("/api/metrics/history").json() == []
    summary = client.get("/api/metrics/summary").json()
    assert summary["status"] == "healthy"
    assert summary["active_total"] == 0
    assert summary["latest"] is None


def test_invalid_limits_rejected(client):
    for path in ("/api/metrics/history", "/api/incidents", "/api/services/history"):
        assert client.get(path, params={"limit": 0}).status_code == 422
        assert client.get(path, params={"limit": 501}).status_code == 422


def test_database_failure_returns_controlled_error(client):
    with (
        patch("app.main.collect_system_metrics", return_value=_reading()),
        patch(
            "sqlalchemy.orm.Session.commit",
            side_effect=RuntimeError("disk I/O exploded /secret/path"),
        ),
    ):
        res = client.get("/api/metrics/current")
    assert res.status_code == 500
    assert res.json()["detail"] == "Database temporarily unavailable. Please retry."
    assert "secret" not in res.text


def test_dashboard_and_static_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "CloudOps Sentinel" in page.text
    assert 'id="toast"' in page.text
    assert 'id="addOkFixture"' in page.text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "pollInFlight" in js.text


def test_service_suggestions_endpoint(client):
    suggestions = client.get("/api/services/suggestions").json()
    paths = {s["path"] for s in suggestions}
    assert paths == {"/api/test/ok", "/api/test/fail"}


def test_simulated_and_observed_stay_separate(client):
    client.post("/api/simulate", json={"scenario": "cpu_warning"})
    with patch("app.main.collect_system_metrics", return_value=_reading()):
        client.get("/api/metrics/current")  # healthy observed reading
    open_incidents = client.get("/api/incidents", params={"status": "open"}).json()
    assert len(open_incidents) == 1
    assert open_incidents[0]["source"] == "simulated"


def test_current_reading_creates_observed_incident(client):
    with patch("app.main.collect_system_metrics", return_value=_reading(cpu=96.0)):
        res = client.get("/api/metrics/current")
    assert res.status_code == 200
    open_incidents = client.get("/api/incidents", params={"status": "open"}).json()
    assert any(
        i["source"] == "observed" and i["severity"] == "critical"
        for i in open_incidents
    )


def test_monitor_cycle_dedup_and_recovery(db):
    db.query(ServiceTarget).delete()
    db.commit()
    with patch("app.main.collect_system_metrics", return_value=_reading(cpu=95.0)):
        run_monitor_cycle()
        run_monitor_cycle()
    active = db.query(Incident).filter(Incident.status != "resolved").all()
    assert len(active) == 1
    assert db.query(MetricSnapshot).count() >= 2

    with patch("app.main.collect_system_metrics", return_value=_reading()):
        run_monitor_cycle()
    assert db.query(Incident).filter(Incident.status != "resolved").count() == 0
    assert active[0].id == db.query(Incident).first().id
