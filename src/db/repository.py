"""Data access layer for simulations and segment parameters."""

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .models import SegmentParamsVersion, SimulationRecord


# ---------------------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------------------


def create_simulation(
    db: Session,
    *,
    simulation_id: str,
    segment_id: str,
    n_variants: int,
    n_simulations: int,
    input_json: str,
) -> SimulationRecord:
    """Insert a new simulation in 'running' status."""
    record = SimulationRecord(
        simulation_id=simulation_id,
        segment_id=segment_id,
        status="running",
        n_variants=n_variants,
        n_simulations=n_simulations,
        input_json=input_json,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def complete_simulation(
    db: Session,
    *,
    simulation_id: str,
    result_json: str,
    recommended_variant_id: str,
    confidence_level: str,
) -> SimulationRecord | None:
    """Mark a simulation as completed and store its result JSON."""
    record = db.get(SimulationRecord, simulation_id)
    if record is None:
        return None
    record.status = "completed"
    record.completed_at = datetime.now(tz=timezone.utc)
    record.result_json = result_json
    record.recommended_variant_id = recommended_variant_id
    record.confidence_level = confidence_level
    db.commit()
    db.refresh(record)
    return record


def fail_simulation(
    db: Session,
    *,
    simulation_id: str,
    error: str,
) -> SimulationRecord | None:
    """Mark a simulation as failed."""
    record = db.get(SimulationRecord, simulation_id)
    if record is None:
        return None
    record.status = "failed"
    record.completed_at = datetime.now(tz=timezone.utc)
    record.error = error
    db.commit()
    db.refresh(record)
    return record


def get_simulation(db: Session, simulation_id: str) -> SimulationRecord | None:
    """Fetch a single simulation by ID."""
    return db.get(SimulationRecord, simulation_id)


def list_simulations(db: Session, *, limit: int = 50) -> list[SimulationRecord]:
    """List simulations ordered by creation time (newest first)."""
    return (
        db.query(SimulationRecord)
        .order_by(SimulationRecord.created_at.desc())
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# Segment parameters
# ---------------------------------------------------------------------------


def save_segment_params(
    db: Session,
    *,
    segment_id: str,
    params: dict[str, Any],
    source: str = "initial",
) -> SegmentParamsVersion:
    """Save a new version of segment parameters."""
    latest = (
        db.query(SegmentParamsVersion)
        .filter_by(segment_id=segment_id)
        .order_by(SegmentParamsVersion.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1

    record = SegmentParamsVersion(
        segment_id=segment_id,
        version=next_version,
        params_json=json.dumps(params, ensure_ascii=False),
        source=source,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_latest_segment_params(
    db: Session, segment_id: str
) -> SegmentParamsVersion | None:
    """Get the most recent parameter version for a segment."""
    return (
        db.query(SegmentParamsVersion)
        .filter_by(segment_id=segment_id)
        .order_by(SegmentParamsVersion.version.desc())
        .first()
    )


def list_segment_versions(
    db: Session, segment_id: str
) -> list[SegmentParamsVersion]:
    """List all parameter versions for a segment."""
    return (
        db.query(SegmentParamsVersion)
        .filter_by(segment_id=segment_id)
        .order_by(SegmentParamsVersion.version.desc())
        .all()
    )
