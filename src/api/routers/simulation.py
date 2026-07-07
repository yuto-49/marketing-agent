"""Simulation endpoints -- create and retrieve simulation results."""

import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db import repository as repo
from src.simulation.schemas import SimulationInput, SimulationStatus

from ..dependencies import get_db, get_engine

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SimulationStarted(BaseModel):
    """Response returned immediately when a simulation is kicked off."""

    simulation_id: str
    status: str = "running"


class SimulationSummary(BaseModel):
    """Lightweight summary used in the list endpoint."""

    simulation_id: str
    segment_id: str
    status: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


def _run_simulation(simulation_id: str, sim_input: SimulationInput) -> None:
    """Execute the simulation in a background task and persist the result."""
    from src.db.database import get_session

    engine = get_engine()
    db = get_session()

    try:
        logger.info("Simulation %s started (segment=%s)", simulation_id, sim_input.segment_id)
        result = engine.run(sim_input)

        stored_result = result.model_copy(
            update={
                "simulation_id": simulation_id,
                "status": SimulationStatus.COMPLETED,
            },
        )
        result_json = stored_result.model_dump_json()

        repo.complete_simulation(
            db,
            simulation_id=simulation_id,
            result_json=result_json,
            recommended_variant_id=stored_result.recommended_variant_id,
            confidence_level=stored_result.confidence_level.value,
        )
        logger.info("Simulation %s completed", simulation_id)

    except Exception:
        logger.exception("Simulation %s failed", simulation_id)
        repo.fail_simulation(
            db,
            simulation_id=simulation_id,
            error="Simulation failed -- see server logs for details",
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=SimulationStarted, status_code=202)
async def start_simulation(
    sim_input: SimulationInput,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> SimulationStarted:
    """Start a new simulation run.

    The simulation executes asynchronously.  Poll ``GET /{simulation_id}``
    to retrieve results once the status transitions to ``completed``.
    """
    simulation_id = str(uuid.uuid4())

    repo.create_simulation(
        db,
        simulation_id=simulation_id,
        segment_id=sim_input.segment_id,
        n_variants=len(sim_input.variants),
        n_simulations=sim_input.n_simulations,
        input_json=sim_input.model_dump_json(),
    )

    background_tasks.add_task(_run_simulation, simulation_id, sim_input)
    logger.info("Queued simulation %s for segment %s", simulation_id, sim_input.segment_id)

    return SimulationStarted(simulation_id=simulation_id)


@router.get("/{simulation_id}")
async def get_simulation(
    simulation_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Retrieve simulation results by ID.

    Returns the full ``SimulationResult`` when completed, or a status
    object while still running / if the run failed.
    """
    record = repo.get_simulation(db, simulation_id)

    if record is None:
        raise HTTPException(status_code=404, detail=f"Simulation '{simulation_id}' not found")

    if record.status == "completed" and record.result_json:
        return json.loads(record.result_json)

    response: dict = {
        "simulation_id": record.simulation_id,
        "segment_id": record.segment_id,
        "status": record.status,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
    if record.error:
        response["error"] = record.error
    return response


@router.get("", response_model=list[SimulationSummary])
async def list_simulations(db: Session = Depends(get_db)) -> list[SimulationSummary]:
    """List all simulations with summary information."""
    records = repo.list_simulations(db)
    return [
        SimulationSummary(
            simulation_id=r.simulation_id,
            segment_id=r.segment_id,
            status=r.status,
            created_at=r.created_at,
        )
        for r in records
    ]
