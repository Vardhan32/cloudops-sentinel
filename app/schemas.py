"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["warning", "critical"]
IncidentStatus = Literal["open", "acknowledged", "resolved"]
MetricSource = Literal["observed", "simulated"]


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    timestamp: datetime
    uptime_seconds: float
    database: str


class MetricReading(BaseModel):
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    uptime_seconds: float
    timestamp: datetime
    source: MetricSource = "observed"


class MetricSnapshotOut(MetricReading):
    id: int


class HealthSummary(BaseModel):
    status: Literal["healthy", "degraded", "critical"]
    active_warning: int
    active_critical: int
    active_total: int
    latest: MetricReading | None = None


class IncidentOut(BaseModel):
    id: int
    type: str
    severity: str
    title: str
    description: str
    observed_value: float | None = None
    threshold: float | None = None
    dedup_key: str
    source: str
    status: str
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}


class ServiceTargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=1024)


class ServiceTargetOut(BaseModel):
    id: int
    name: str
    url: str
    enabled: bool

    model_config = {"from_attributes": True}


class ServiceCheckOut(BaseModel):
    id: int
    service_id: int
    timestamp: datetime
    status_code: int | None = None
    response_time_ms: float | None = None
    healthy: bool
    error: str = ""

    model_config = {"from_attributes": True}


class ServiceStatusOut(BaseModel):
    target: ServiceTargetOut
    latest: ServiceCheckOut | None = None


class SimulateIn(BaseModel):
    scenario: Literal[
        "cpu_warning",
        "cpu_critical",
        "memory_critical",
        "disk_critical",
        "service_failure",
        "clear",
    ]
