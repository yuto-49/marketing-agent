"""Comparison endpoint -- side-by-side variant comparison."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.db import repository as repo
from src.simulation.schemas import SimulationResult

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_completed_result(simulation_id: str, db: Session) -> SimulationResult:
    """Look up a simulation from DB and ensure it is completed."""
    record = repo.get_simulation(db, simulation_id)

    if record is None:
        raise HTTPException(status_code=404, detail=f"Simulation '{simulation_id}' not found")

    if record.status == "running":
        raise HTTPException(status_code=409, detail="Simulation is still running")
    if record.status == "failed":
        raise HTTPException(status_code=500, detail="Simulation failed")
    if not record.result_json:
        raise HTTPException(status_code=500, detail="No result data available")

    return SimulationResult.model_validate_json(record.result_json)


def _build_confidence_statement_ja(tau_sim: float, ci_lower: float, ci_upper: float) -> str:
    """Generate a Japanese confidence statement based on treatment effect and CI.

    Rules:
    - tau > 0 and CI doesn't cross 0: high confidence the variant outperforms control.
    - tau > 0 but CI crosses 0: possible improvement but not statistically certain.
    - tau <= 0: predicted to match or underperform control.
    """
    if tau_sim > 0 and ci_lower > 0:
        return "高い確信度でControlを上回ります"
    if tau_sim > 0:
        return "Controlを上回る可能性がありますが、統計的に確実ではありません"
    return "Controlと同等かそれ以下の結果が予測されます"


@router.get("/{simulation_id}/compare")
async def compare_variants(simulation_id: str, db: Session = Depends(get_db)) -> dict:
    """Return structured comparison data for side-by-side variant analysis.

    Structure::

        {
            "variants": [
                {
                    "variant_id": "...",
                    "variant_name": "...",
                    "is_control": false,
                    "is_recommended": true,
                    "funnel_metrics": {
                        "delivery": { "rate": 0.95, "ci": [0.93, 0.97], "vs_control": 0.02 },
                        ...
                    },
                    "overall_conversion": 0.05,
                    "conversion_ci": [0.04, 0.06],
                    "tau_sim": 0.012,
                    "confidence_statement_ja": "..."
                }
            ]
        }
    """
    result = _require_completed_result(simulation_id, db)

    # Find the control variant's funnel rates for delta calculation.
    control_rates: dict[str, float] = {}
    for variant in result.variants:
        if variant.variant_id == "control_bau":
            for step in variant.funnel_steps:
                control_rates[step.stage.value] = step.predicted_rate
            break

    comparison_variants: list[dict] = []

    for variant in result.variants:
        is_control = variant.variant_id == "control_bau"
        is_recommended = variant.variant_id == result.recommended_variant_id

        funnel_metrics: dict[str, dict] = {}
        for step in variant.funnel_steps:
            stage_key = step.stage.value
            control_rate = control_rates.get(stage_key)
            vs_control = (
                round(step.predicted_rate - control_rate, 6)
                if control_rate is not None and not is_control
                else None
            )
            funnel_metrics[stage_key] = {
                "rate": step.predicted_rate,
                "ci": [step.ci_lower, step.ci_upper],
                "vs_control": vs_control,
            }

        ci_lower, ci_upper = variant.conversion_ci
        confidence_statement_ja = _build_confidence_statement_ja(
            variant.tau_sim, ci_lower, ci_upper
        )

        comparison_variants.append(
            {
                "variant_id": variant.variant_id,
                "variant_name": variant.variant_name,
                "is_control": is_control,
                "is_recommended": is_recommended,
                "funnel_metrics": funnel_metrics,
                "overall_conversion": variant.overall_conversion_rate,
                "conversion_ci": [ci_lower, ci_upper],
                "tau_sim": variant.tau_sim,
                "confidence_statement_ja": confidence_statement_ja,
            }
        )

    return {"variants": comparison_variants}
