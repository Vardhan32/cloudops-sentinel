"""Threshold evaluation + incident lifecycle engine.

Design rules:
- One active incident per condition (dedup_key). Repeated monitor cycles
  update the existing incident instead of creating duplicates.
- When a condition clears, the active incident is marked resolved
  (recovery recorded with a timestamp).
- Genuine readings use source="observed"; demo fixtures use
  source="simulated" so they are never confused.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from . import config
from .models import Incident

ACTIVE_STATUSES = ("open", "acknowledged")

SEVERITY_RANK = {"warning": 1, "critical": 2}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_thresholds(
    cpu: float, memory: float, disk: float
) -> list[dict[str, Any]]:
    """Return a finding per breached threshold (empty when healthy)."""
    findings: list[dict[str, Any]] = []
    checks = [
        ("cpu_high", "cpu", "High CPU utilization", cpu, config.CPU_WARN, config.CPU_CRIT, "%"),
        ("memory_high", "memory", "High memory utilization", memory, config.MEM_WARN, config.MEM_CRIT, "%"),
        ("disk_low", "disk", "Low disk space", disk, config.DISK_WARN, config.DISK_CRIT, "% used"),
    ]
    for itype, dedup, title, value, warn, crit, unit in checks:
        severity = None
        threshold = None
        if value >= crit:
            severity, threshold = "critical", crit
        elif value >= warn:
            severity, threshold = "warning", warn
        if severity:
            findings.append(
                {
                    "type": itype,
                    "dedup_key": dedup,
                    "severity": severity,
                    "title": f"{title} ({severity})",
                    "description": (
                        f"{title}: observed {value:.1f}% against "
                        f"{severity} threshold {threshold:.1f}%."
                    ),
                    "observed_value": float(value),
                    "threshold": float(threshold),
                    "unit": unit,
                }
            )
    return findings


def sync_incidents(
    db: Session, findings: list[dict[str, Any]], source: str = "observed"
) -> list[Incident]:
    """Reconcile active incidents with current findings.

    - New condition -> create one incident.
    - Ongoing condition -> update value/severity/timestamp, no duplicate.
    - Cleared condition -> resolve the active incident (recovery).
    Returns all currently-relevant incidents (created + updated).
    """
    touched: list[Incident] = []
    active = (
        db.query(Incident)
        .filter(Incident.status.in_(ACTIVE_STATUSES), Incident.source == source)
        .all()
    )
    by_key = {i.dedup_key: i for i in active}
    seen_keys = set()

    for finding in findings:
        key = finding["dedup_key"]
        seen_keys.add(key)
        existing = by_key.get(key)
        if existing is None:
            incident = Incident(
                type=finding["type"],
                severity=finding["severity"],
                title=finding["title"],
                description=finding["description"],
                observed_value=finding.get("observed_value"),
                threshold=finding.get("threshold"),
                dedup_key=key,
                source=source,
                status="open",
            )
            db.add(incident)
            touched.append(incident)
        else:
            # Escalate severity if it worsened, never silently downgrade
            # an acknowledged critical incident within the same cycle set.
            if SEVERITY_RANK.get(finding["severity"], 0) > SEVERITY_RANK.get(
                existing.severity, 0
            ):
                existing.severity = finding["severity"]
                existing.title = finding["title"]
            existing.observed_value = finding.get("observed_value")
            existing.threshold = finding.get("threshold")
            existing.description = finding["description"]
            existing.updated_at = _utcnow()
            touched.append(existing)

    # Recovery: active incidents whose condition no longer holds.
    for incident in active:
        if incident.dedup_key not in seen_keys and incident.type in (
            "cpu_high",
            "memory_high",
            "disk_low",
        ):
            incident.status = "resolved"
            incident.resolved_at = _utcnow()
            incident.updated_at = _utcnow()
            incident.description = (
                incident.description
                + " Condition cleared; system recovered."
            )

    db.commit()
    for incident in touched:
        db.refresh(incident)
    return touched


def record_service_incident(
    db: Session,
    service_name: str,
    error: str,
    *,
    source: str = "observed",
) -> Incident:
    """Create or update the active incident for a failed service check."""
    key = f"service:{service_name}"
    existing = (
        db.query(Incident)
        .filter(
            Incident.dedup_key == key,
            Incident.status.in_(ACTIVE_STATUSES),
            Incident.source == source,
        )
        .order_by(Incident.id.desc())
        .first()
    )
    now = _utcnow()
    if existing is None:
        incident = Incident(
            type="service_down",
            severity="critical",
            title=f"Service unhealthy: {service_name}",
            description=f"Health check failed for '{service_name}': {error}",
            dedup_key=key,
            source=source,
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)
        return incident
    existing.severity = "critical"
    existing.description = f"Health check failed for '{service_name}': {error}"
    existing.updated_at = now
    db.commit()
    db.refresh(existing)
    return existing


def resolve_service_incident(
    db: Session, service_name: str, *, source: str = "observed"
) -> Incident | None:
    """Mark the active incident for a service as resolved (recovery)."""
    key = f"service:{service_name}"
    existing = (
        db.query(Incident)
        .filter(
            Incident.dedup_key == key,
            Incident.status.in_(ACTIVE_STATUSES),
            Incident.source == source,
        )
        .order_by(Incident.id.desc())
        .first()
    )
    if existing is None:
        return None
    now = _utcnow()
    existing.status = "resolved"
    existing.resolved_at = now
    existing.updated_at = now
    existing.description += " Service recovered; health check passing."
    db.commit()
    db.refresh(existing)
    return existing


def acknowledge_incident(db: Session, incident_id: int) -> Incident | None:
    incident = db.get(Incident, incident_id)
    if incident is None or incident.status != "open":
        return None
    incident.status = "acknowledged"
    incident.acknowledged_at = _utcnow()
    incident.updated_at = _utcnow()
    db.commit()
    db.refresh(incident)
    return incident


def resolve_incident(db: Session, incident_id: int) -> Incident | None:
    incident = db.get(Incident, incident_id)
    if incident is None or incident.status == "resolved":
        return None
    incident.status = "resolved"
    incident.resolved_at = _utcnow()
    incident.updated_at = _utcnow()
    db.commit()
    db.refresh(incident)
    return incident
