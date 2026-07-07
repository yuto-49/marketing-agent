"""Evaluation endpoints -- compare sim predictions against real outcomes."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.evaluation.metrics import (
    compute_calibration,
    compute_ranking_metrics,
)

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class VariantOutcome(BaseModel):
    """Real outcome for a single variant."""

    variant_id: str
    real_rank: int = Field(..., ge=1)
    tau_real: Optional[float] = None


class EvaluationRequest(BaseModel):
    """Request to evaluate sim predictions against real outcomes."""

    simulation_id: str
    outcomes: list[VariantOutcome] = Field(..., min_length=2)
    k: int = Field(3, ge=1)
    wildcard_id: Optional[str] = None


class RankingResult(BaseModel):
    hit_at_1: bool
    hit_at_3: bool
    hit_at_k: bool
    k: int
    rank_corr: float
    rank_corr_pvalue: float
    n_variants: int
    sim_winner: str
    real_winner: str
    wildcard_beat_rate: Optional[float]


class CalibrationResult(BaseModel):
    beta: float
    intercept: float
    r_squared: float
    n_observations: int


class EvaluationResponse(BaseModel):
    simulation_id: str
    ranking: RankingResult
    calibration: Optional[CalibrationResult] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/evaluate", response_model=EvaluationResponse)
async def evaluate_simulation(
    req: EvaluationRequest,
    db: Session = Depends(get_db),
) -> EvaluationResponse:
    """Compare a completed simulation's predicted ranks against real outcomes.

    Supply the real ranks (and optionally tau_real) for each variant.
    Returns Hit@k, Spearman rank correlation, and optional calibration metrics.
    """
    from src.db import repository as repo
    from src.simulation.schemas import SimulationResult

    record = repo.get_simulation(db, req.simulation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Simulation not found")
    if record.status != "completed" or not record.result_json:
        raise HTTPException(status_code=409, detail="Simulation not yet completed")

    result = SimulationResult.model_validate_json(record.result_json)

    # Build sim ranks lookup
    sim_rank_map = {v.variant_id: v.predicted_rank for v in result.variants}
    sim_tau_map = {v.variant_id: v.tau_sim for v in result.variants}

    # Match outcomes to sim variants
    variant_ids: list[str] = []
    sim_ranks: list[int] = []
    real_ranks: list[int] = []
    tau_sim_list: list[float] = []
    tau_real_list: list[float] = []

    for outcome in req.outcomes:
        if outcome.variant_id not in sim_rank_map:
            raise HTTPException(
                status_code=400,
                detail=f"Variant '{outcome.variant_id}' not found in simulation results",
            )
        variant_ids.append(outcome.variant_id)
        sim_ranks.append(sim_rank_map[outcome.variant_id])
        real_ranks.append(outcome.real_rank)

        if outcome.tau_real is not None:
            tau_sim_list.append(sim_tau_map[outcome.variant_id])
            tau_real_list.append(outcome.tau_real)

    ranking = compute_ranking_metrics(
        variant_ids=variant_ids,
        sim_ranks=sim_ranks,
        real_ranks=real_ranks,
        k=req.k,
        wildcard_id=req.wildcard_id,
    )

    calibration = None
    if len(tau_sim_list) >= 3:
        cal = compute_calibration(tau_sim_list, tau_real_list)
        calibration = CalibrationResult(
            beta=cal.beta,
            intercept=cal.intercept,
            r_squared=cal.r_squared,
            n_observations=cal.n_observations,
        )

    return EvaluationResponse(
        simulation_id=req.simulation_id,
        ranking=RankingResult(
            hit_at_1=ranking.hit_at_1,
            hit_at_3=ranking.hit_at_3,
            hit_at_k=ranking.hit_at_k,
            k=ranking.k,
            rank_corr=ranking.rank_corr,
            rank_corr_pvalue=ranking.rank_corr_pvalue,
            n_variants=ranking.n_variants,
            sim_winner=ranking.sim_winner,
            real_winner=ranking.real_winner,
            wildcard_beat_rate=ranking.wildcard_beat_rate,
        ),
        calibration=calibration,
    )
