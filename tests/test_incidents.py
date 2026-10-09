"""Incident lifecycle: creation, dedup, ack, resolve, recovery."""

from app import monitor


def _finding(key="cpu", severity="warning", itype="cpu_high"):
    return {
        "type": itype,
        "dedup_key": key,
        "severity": severity,
        "title": f"{key} {severity}",
        "description": "test",
        "observed_value": 80.0,
        "threshold": 75.0,
    }


def test_incident_creation_and_dedup(db):
    first = monitor.sync_incidents(db, [_finding()], source="observed")
    assert len(first) == 1
    second = monitor.sync_incidents(db, [_finding()], source="observed")
    assert second[0].id == first[0].id  # no duplicate

    from app.models import Incident

    count = db.query(Incident).filter(Incident.status != "resolved").count()
    assert count == 1


def test_severity_escalation(db):
    created = monitor.sync_incidents(db, [_finding(severity="warning")], source="observed")
    updated = monitor.sync_incidents(db, [_finding(severity="critical")], source="observed")
    assert updated[0].id == created[0].id
    assert updated[0].severity == "critical"


def test_recovery_resolves_incident(db):
    monitor.sync_incidents(db, [_finding()], source="observed")
    monitor.sync_incidents(db, [], source="observed")  # condition cleared
    from app.models import Incident

    incident = db.query(Incident).first()
    assert incident.status == "resolved"
    assert incident.resolved_at is not None


def test_acknowledge_and_resolve_via_api(client):
    res = client.post("/api/simulate", json={"scenario": "cpu_warning"})
    assert res.status_code == 200
    incident_id = res.json()["incident_ids"][0]

    ack = client.post(f"/api/incidents/{incident_id}/acknowledge")
    assert ack.status_code == 200
    assert ack.json()["status"] == "acknowledged"

    # Acknowledging twice is a conflict.
    assert client.post(f"/api/incidents/{incident_id}/acknowledge").status_code == 409

    done = client.post(f"/api/incidents/{incident_id}/resolve")
    assert done.status_code == 200
    assert done.json()["status"] == "resolved"

    assert client.post(f"/api/incidents/{incident_id}/resolve").status_code == 409


def test_simulated_recovery_clears_simulated_only(client):
    client.post("/api/simulate", json={"scenario": "cpu_critical"})
    res = client.post("/api/simulate", json={"scenario": "clear"})
    assert res.json()["resolved"] >= 1
    incidents = client.get("/api/incidents", params={"status": "open"}).json()
    assert all(i["source"] != "simulated" for i in incidents)
