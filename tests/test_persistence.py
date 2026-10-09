"""Database persistence: data survives across sessions."""

from app.database import SessionLocal
from app.models import MetricSnapshot


def test_persistence_across_sessions(db):
    db.add(
        MetricSnapshot(
            cpu_percent=10.0,
            memory_percent=20.0,
            disk_percent=30.0,
            uptime_seconds=1.0,
            source="observed",
        )
    )
    db.commit()

    fresh = SessionLocal()
    try:
        rows = fresh.query(MetricSnapshot).all()
        assert len(rows) >= 1
        assert rows[-1].cpu_percent == 10.0
    finally:
        fresh.close()
