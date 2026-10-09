"""Metrics collection (mocked psutil) and threshold evaluation tests."""

from unittest.mock import patch

from app import monitor
from app.metrics import collect_system_metrics


def test_collect_metrics_mocked():
    with (
        patch("app.metrics.psutil.cpu_percent", return_value=12.5),
        patch("app.metrics.psutil.virtual_memory") as vm,
        patch("app.metrics.psutil.disk_usage") as du,
        patch("app.metrics.psutil.boot_time", return_value=1000.0),
    ):
        vm.return_value.percent = 40.0
        du.return_value.percent = 55.0
        reading = collect_system_metrics()
    assert reading["cpu_percent"] == 12.5
    assert reading["memory_percent"] == 40.0
    assert reading["disk_percent"] == 55.0
    assert reading["source"] == "observed"


def test_thresholds_healthy():
    assert monitor.evaluate_thresholds(10.0, 20.0, 30.0) == []


def test_thresholds_warning():
    findings = monitor.evaluate_thresholds(80.0, 20.0, 30.0)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["dedup_key"] == "cpu"


def test_thresholds_critical():
    findings = monitor.evaluate_thresholds(95.0, 96.0, 97.0)
    by_key = {f["dedup_key"]: f for f in findings}
    assert by_key["cpu"]["severity"] == "critical"
    assert by_key["memory"]["severity"] == "critical"
    assert by_key["disk"]["severity"] == "critical"


def test_metrics_current_endpoint_mocked(client):
    with (
        patch("app.main.collect_system_metrics") as collect,
    ):
        from datetime import datetime, timezone

        collect.return_value = {
            "cpu_percent": 11.0,
            "memory_percent": 22.0,
            "disk_percent": 33.0,
            "uptime_seconds": 5.0,
            "timestamp": datetime.now(timezone.utc),
            "source": "observed",
        }
        res = client.get("/api/metrics/current")
    assert res.status_code == 200
    body = res.json()
    assert body["cpu_percent"] == 11.0
    assert body["source"] == "observed"

    history = client.get("/api/metrics/history?limit=5").json()
    assert any(h["cpu_percent"] == 11.0 for h in history)
