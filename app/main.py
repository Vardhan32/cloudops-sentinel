"""CloudOps Sentinel FastAPI application."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import config, monitor
from .database import get_db, init_db
from .metrics import collect_system_metrics
from .models import Incident, MetricSnapshot, ServiceCheckResult, ServiceTarget
from .schemas import (
    HealthResponse,
    HealthSummary,
    IncidentOut,
    MetricReading,
    MetricSnapshotOut,
    ServiceCheckOut,
    ServiceStatusOut,
    ServiceTargetIn,
    ServiceTargetOut,
    SimulateIn,
)
from .services import check_service, validate_service_name, validate_service_url

START_TIME = time.time()
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _monitor_enabled() -> bool:
    return os.getenv("SENTINEL_ENABLE_MONITOR", "1") == "1"


def run_monitor_cycle() -> None:
    """One synchronous monitoring pass: metrics + service checks."""
    from .database import SessionLocal

    db: Session = SessionLocal()
    try:
        reading = collect_system_metrics()
        snapshot = MetricSnapshot(
            cpu_percent=reading["cpu_percent"],
            memory_percent=reading["memory_percent"],
            disk_percent=reading["disk_percent"],
            uptime_seconds=reading["uptime_seconds"],
            source="observed",
        )
        db.add(snapshot)
        db.commit()
        findings = monitor.evaluate_thresholds(
            reading["cpu_percent"],
            reading["memory_percent"],
            reading["disk_percent"],
        )
        monitor.sync_incidents(db, findings, source="observed")

        targets = db.query(ServiceTarget).filter(ServiceTarget.enabled.is_(True)).all()
        for target in targets:
            result = check_service(target.url)
            db.add(
                ServiceCheckResult(
                    service_id=target.id,
                    status_code=result["status_code"],
                    response_time_ms=result["response_time_ms"],
                    healthy=result["healthy"],
                    error=result["error"],
                )
            )
            db.commit()
            if result["healthy"]:
                monitor.resolve_service_incident(db, target.name, source="observed")
            else:
                monitor.record_service_incident(
                    db, target.name, result["error"], source="observed"
                )
        # Keep history bounded (retain latest 5000 snapshots / 2000 results).
        _trim_history(db)
    except Exception:
        db.rollback()
    finally:
        db.close()


def _trim_history(db: Session) -> None:
    for model, keep in ((MetricSnapshot, 5000), (ServiceCheckResult, 2000)):
        count = db.query(model).count()
        if count > keep:
            oldest_ids = [
                r[0]
                for r in db.query(model.id).order_by(model.id.asc()).limit(count - keep).all()
            ]
            db.query(model).filter(model.id.in_(oldest_ids)).delete(
                synchronize_session=False
            )
            db.commit()


async def _monitor_loop() -> None:
    while True:
        await asyncio.to_thread(run_monitor_cycle)
        await asyncio.sleep(config.MONITOR_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Seed default service targets on first run.
    from .database import SessionLocal

    db = SessionLocal()
    try:
        if db.query(ServiceTarget).count() == 0:
            for svc in config.DEFAULT_SERVICES:
                db.add(ServiceTarget(name=svc["name"], url=svc["url"]))
            db.commit()
    finally:
        db.close()
    task = None
    if _monitor_enabled():
        task = asyncio.create_task(_monitor_loop())
    yield
    if task is not None:
        task.cancel()


app = FastAPI(
    title="CloudOps Sentinel",
    version=config.APP_VERSION,
    description="Local-first infrastructure monitoring and incident troubleshooting.",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": exc.errors()},
    )


# ---------- Health ----------


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
def api_health(db: Session = Depends(get_db)):
    try:
        db.query(MetricSnapshot).limit(1).count()
        db_status = "ok"
    except Exception:
        db_status = "error"
    return HealthResponse(
        status="ok",
        app=config.APP_NAME,
        version=config.APP_VERSION,
        timestamp=_utcnow(),
        uptime_seconds=round(time.time() - START_TIME, 1),
        database=db_status,
    )


# ---------- Metrics ----------


@app.get("/api/metrics/current", response_model=MetricSnapshotOut, tags=["metrics"])
def metrics_current(db: Session = Depends(get_db)):
    try:
        reading = collect_system_metrics()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    snapshot = MetricSnapshot(
        cpu_percent=reading["cpu_percent"],
        memory_percent=reading["memory_percent"],
        disk_percent=reading["disk_percent"],
        uptime_seconds=reading["uptime_seconds"],
        source="observed",
    )
    db.add(snapshot)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")
    db.refresh(snapshot)
    return MetricSnapshotOut(
        id=snapshot.id,
        cpu_percent=snapshot.cpu_percent,
        memory_percent=snapshot.memory_percent,
        disk_percent=snapshot.disk_percent,
        uptime_seconds=snapshot.uptime_seconds,
        timestamp=snapshot.timestamp,
        source="observed",
    )


@app.get("/api/metrics/history", response_model=list[MetricSnapshotOut], tags=["metrics"])
def metrics_history(
    limit: int = Query(default=60, ge=1, le=500),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(MetricSnapshot)
        .order_by(MetricSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    return [
        MetricSnapshotOut(
            id=r.id,
            cpu_percent=r.cpu_percent,
            memory_percent=r.memory_percent,
            disk_percent=r.disk_percent,
            uptime_seconds=r.uptime_seconds,
            timestamp=r.timestamp,
            source=r.source,  # type: ignore[typeddict-item]
        )
        for r in reversed(rows)
    ]


@app.get("/api/metrics/summary", response_model=HealthSummary, tags=["metrics"])
def metrics_summary(db: Session = Depends(get_db)):
    latest_row = (
        db.query(MetricSnapshot).order_by(MetricSnapshot.id.desc()).first()
    )
    latest = None
    if latest_row is not None:
        latest = MetricReading(
            cpu_percent=latest_row.cpu_percent,
            memory_percent=latest_row.memory_percent,
            disk_percent=latest_row.disk_percent,
            uptime_seconds=latest_row.uptime_seconds,
            timestamp=latest_row.timestamp,
            source=latest_row.source,  # type: ignore[typeddict-item]
        )
    warn = (
        db.query(Incident)
        .filter(Incident.status.in_(("open", "acknowledged")), Incident.severity == "warning")
        .count()
    )
    crit = (
        db.query(Incident)
        .filter(Incident.status.in_(("open", "acknowledged")), Incident.severity == "critical")
        .count()
    )
    status = "healthy" if crit == 0 and warn == 0 else ("critical" if crit > 0 else "degraded")
    return HealthSummary(
        status=status,
        active_warning=warn,
        active_critical=crit,
        active_total=warn + crit,
        latest=latest,
    )


# ---------- Incidents ----------


@app.get("/api/incidents", response_model=list[IncidentOut], tags=["incidents"])
def list_incidents(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(Incident).order_by(Incident.id.desc())
    if status is not None:
        if status not in ("open", "acknowledged", "resolved"):
            raise HTTPException(status_code=422, detail="Invalid status filter.")
        q = q.filter(Incident.status == status)
    if severity is not None:
        if severity not in ("warning", "critical"):
            raise HTTPException(status_code=422, detail="Invalid severity filter.")
        q = q.filter(Incident.severity == severity)
    return q.limit(limit).all()


@app.get("/api/incidents/{incident_id}", response_model=IncidentOut, tags=["incidents"])
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return incident


@app.post("/api/incidents/{incident_id}/acknowledge", response_model=IncidentOut, tags=["incidents"])
def acknowledge(incident_id: int, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    if incident.status != "open":
        raise HTTPException(
            status_code=409,
            detail=f"Only open incidents can be acknowledged (current: {incident.status}).",
        )
    return monitor.acknowledge_incident(db, incident_id)


@app.post("/api/incidents/{incident_id}/resolve", response_model=IncidentOut, tags=["incidents"])
def resolve(incident_id: int, db: Session = Depends(get_db)):
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    if incident.status == "resolved":
        raise HTTPException(status_code=409, detail="Incident is already resolved.")
    return monitor.resolve_incident(db, incident_id)


# ---------- Services ----------


@app.get("/api/services", response_model=list[ServiceStatusOut], tags=["services"])
def list_services(db: Session = Depends(get_db)):
    targets = db.query(ServiceTarget).order_by(ServiceTarget.id.asc()).all()
    out = []
    for target in targets:
        latest = (
            db.query(ServiceCheckResult)
            .filter(ServiceCheckResult.service_id == target.id)
            .order_by(ServiceCheckResult.id.desc())
            .first()
        )
        out.append(
            ServiceStatusOut(
                target=ServiceTargetOut.model_validate(target),
                latest=ServiceCheckOut.model_validate(latest) if latest else None,
            )
        )
    return out


@app.post("/api/services", response_model=ServiceTargetOut, status_code=201, tags=["services"])
def add_service(payload: ServiceTargetIn, db: Session = Depends(get_db)):
    try:
        name = validate_service_name(payload.name)
        url = validate_service_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if db.query(ServiceTarget).filter(ServiceTarget.name == name).first():
        raise HTTPException(status_code=409, detail="A service with this name already exists.")
    target = ServiceTarget(name=name, url=url)
    db.add(target)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")
    db.refresh(target)
    return target


@app.delete("/api/services/{service_id}", status_code=204, tags=["services"])
def delete_service(service_id: int, db: Session = Depends(get_db)):
    target = db.get(ServiceTarget, service_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Service not found.")
    db.query(ServiceCheckResult).filter(ServiceCheckResult.service_id == service_id).delete()
    db.delete(target)
    db.commit()
    return None


@app.post("/api/services/{service_id}/check", response_model=ServiceCheckOut, tags=["services"])
def check_service_now(service_id: int, db: Session = Depends(get_db)):
    target = db.get(ServiceTarget, service_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Service not found.")
    # Only the stored, validated URL is ever requested (no user-supplied URL).
    result = check_service(target.url)
    row = ServiceCheckResult(
        service_id=target.id,
        status_code=result["status_code"],
        response_time_ms=result["response_time_ms"],
        healthy=result["healthy"],
        error=result["error"],
    )
    db.add(row)
    db.commit()
    if result["healthy"]:
        monitor.resolve_service_incident(db, target.name, source="observed")
    else:
        monitor.record_service_incident(db, target.name, result["error"], source="observed")
    db.refresh(row)
    return row


@app.get("/api/services/history", response_model=list[ServiceCheckOut], tags=["services"])
def service_history(
    service_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(ServiceCheckResult).order_by(ServiceCheckResult.id.desc())
    if service_id is not None:
        q = q.filter(ServiceCheckResult.service_id == service_id)
    return q.limit(limit).all()


# ---------- Controlled test fixtures (offline demo, no internet needed) ----------


@app.get("/api/test/ok", tags=["test-fixtures"])
def test_ok():
    return {"status": "ok", "message": "Test fixture is healthy."}


@app.get("/api/test/fail", tags=["test-fixtures"])
def test_fail():
    return JSONResponse(status_code=503, content={"status": "fail", "message": "Simulated failure."})


# ---------- Demo simulation (explicitly labelled, never touches the host) ----------

SIMULATED_READINGS: dict[str, dict] = {
    "cpu_warning": {"cpu_percent": 82.0, "memory_percent": 45.0, "disk_percent": 40.0},
    "cpu_critical": {"cpu_percent": 96.0, "memory_percent": 50.0, "disk_percent": 40.0},
    "memory_critical": {"cpu_percent": 30.0, "memory_percent": 94.0, "disk_percent": 40.0},
    "disk_critical": {"cpu_percent": 30.0, "memory_percent": 45.0, "disk_percent": 95.0},
}


@app.post("/api/simulate", tags=["demo"])
def simulate(payload: SimulateIn, db: Session = Depends(get_db)):
    """Run a deterministic demo scenario. Creates SIMULATED data only.

    'clear' resolves all simulated incidents (recovery walkthrough).
    """
    if payload.scenario == "clear":
        now = _utcnow()
        active = (
            db.query(Incident)
            .filter(
                Incident.source == "simulated",
                Incident.status.in_(("open", "acknowledged")),
            )
            .all()
        )
        for incident in active:
            incident.status = "resolved"
            incident.resolved_at = now
            incident.updated_at = now
            incident.description += " Simulated condition cleared; recovered."
        db.commit()
        return {"scenario": "clear", "resolved": len(active)}

    if payload.scenario == "service_failure":
        incident = monitor.record_service_incident(
            db, "demo-service", "Simulated connection failure (demo).", source="simulated"
        )
        return {"scenario": payload.scenario, "incident_id": incident.id}

    values = SIMULATED_READINGS[payload.scenario]
    snapshot = MetricSnapshot(
        cpu_percent=values["cpu_percent"],
        memory_percent=values["memory_percent"],
        disk_percent=values["disk_percent"],
        uptime_seconds=0.0,
        source="simulated",
    )
    db.add(snapshot)
    db.commit()
    findings = monitor.evaluate_thresholds(
        values["cpu_percent"], values["memory_percent"], values["disk_percent"]
    )
    incidents = monitor.sync_incidents(db, findings, source="simulated")
    return {
        "scenario": payload.scenario,
        "snapshot_id": snapshot.id,
        "incident_ids": [i.id for i in incidents],
    }


# ---------- Dashboard (static frontend) ----------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"app": config.APP_NAME, "docs": "/docs"}
