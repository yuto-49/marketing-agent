"""Simulation endpoints -- create and retrieve simulation results."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from src.simulation.schemas import SimulationInput, SimulationResult, SimulationStatus

from ..dependencies import get_engine, get_results_store

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
    """Execute the simulation in a background task and store the result."""
    store = get_results_store()
    engine = get_engine()

    try:
        logger.info("Simulation %s started (segment=%s)", simulation_id, sim_input.segment_id)
        result = engine.run(sim_input)

        # Replace the engine-generated ID with the one we already returned
        # to the caller.  SimulationResult is frozen, so rebuild it.
        stored_result = result.model_copy(
            update={
                "simulation_id": simulation_id,
                "status": SimulationStatus.COMPLETED,
            },
        )
        store[simulation_id] = stored_result
        logger.info("Simulation %s completed", simulation_id)

    except Exception:
        logger.exception("Simulation %s failed", simulation_id)
        store[simulation_id] = {
            "simulation_id": simulation_id,
            "segment_id": sim_input.segment_id,
            "status": SimulationStatus.FAILED.value,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "error": "Simulation failed -- see server logs for details",
        }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=SimulationStarted, status_code=202)
async def start_simulation(
    sim_input: SimulationInput,
    background_tasks: BackgroundTasks,
) -> SimulationStarted:
    """Start a new simulation run.

    The simulation executes asynchronously.  Poll ``GET /{simulation_id}``
    to retrieve results once the status transitions to ``completed``.
    """
    simulation_id = str(uuid.uuid4())
    store = get_results_store()

    # Pre-populate store so the GET endpoint can report "running".
    store[simulation_id] = {
        "simulation_id": simulation_id,
        "segment_id": sim_input.segment_id,
        "status": SimulationStatus.RUNNING.value,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    background_tasks.add_task(_run_simulation, simulation_id, sim_input)
    logger.info("Queued simulation %s for segment %s", simulation_id, sim_input.segment_id)

    return SimulationStarted(simulation_id=simulation_id)


@router.get("/{simulation_id}")
async def get_simulation(simulation_id: str) -> dict:
    """Retrieve simulation results by ID.

    Returns the full ``SimulationResult`` when completed, or a status
    object while still running / if the run failed.
    """
    store = get_results_store()
    entry = store.get(simulation_id)

    if entry is None:
        raise HTTPException(status_code=404, detail=f"Simulation '{simulation_id}' not found")

    if isinstance(entry, SimulationResult):
        return entry.model_dump(mode="json")

    # Still running or failed -- return the lightweight dict.
    return entry


@router.get("", response_model=list[SimulationSummary])
async def list_simulations() -> list[SimulationSummary]:
    """List all simulations with summary information."""
    store = get_results_store()
    summaries: list[SimulationSummary] = []

    for entry in store.values():
        if isinstance(entry, SimulationResult):
            summaries.append(
                SimulationSummary(
                    simulation_id=entry.simulation_id,
                    segment_id=entry.segment_id,
                    status=entry.status.value,
                    created_at=entry.created_at,
                )
            )
        elif isinstance(entry, dict):
            summaries.append(
                SimulationSummary(
                    simulation_id=entry["simulation_id"],
                    segment_id=entry["segment_id"],
                    status=entry["status"],
                    created_at=entry.get("created_at", datetime.now(tz=timezone.utc).isoformat()),
                )
            )

    return summaries
