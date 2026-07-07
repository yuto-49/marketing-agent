"""Explanation endpoints -- drivers and walkthrough for simulation results."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.db import repository as repo
from src.explainability.labels import FEATURE_LABELS_JA, get_description, get_label
from src.simulation.schemas import FunnelStage, SimulationResult

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


@router.get("/{simulation_id}/drivers")
async def get_drivers(simulation_id: str, db: Session = Depends(get_db)) -> dict:
    """Return per-variant SHAP feature importance data.

    Structure::

        {
            "variants": {
                "<variant_id>": {
                    "overall": [ {feature_name, label_ja, shap_value, direction, description_ja}, ... ],
                    "delivery": [ ... ],
                    "open": [ ... ],
                    ...
                }
            }
        }
    """
    result = _require_completed_result(simulation_id, db)

    variants_data: dict[str, dict] = {}

    for variant in result.variants:
        stage_drivers: dict[str, list[dict]] = {}

        # Collect per-stage drivers from funnel steps.
        for step in variant.funnel_steps:
            drivers_list = [
                {
                    "feature_name": d.feature_name,
                    "label_ja": d.feature_label_ja,
                    "shap_value": d.shap_value,
                    "direction": d.direction.value,
                    "description_ja": d.description_ja,
                }
                for d in step.top_drivers
            ]
            stage_drivers[step.stage.value] = drivers_list

        # Build an "overall" aggregation: merge drivers across stages,
        # summing SHAP values by feature.
        overall_map: dict[str, float] = {}
        for step in variant.funnel_steps:
            for d in step.top_drivers:
                overall_map[d.feature_name] = overall_map.get(d.feature_name, 0.0) + d.shap_value

        overall_list = sorted(
            [
                {
                    "feature_name": fname,
                    "label_ja": get_label(fname),
                    "shap_value": round(total, 6),
                    "direction": "positive" if total >= 0 else "negative",
                    "description_ja": get_description(fname),
                }
                for fname, total in overall_map.items()
            ],
            key=lambda x: abs(x["shap_value"]),
            reverse=True,
        )

        stage_drivers["overall"] = overall_list
        variants_data[variant.variant_id] = stage_drivers

    return {"variants": variants_data}


@router.get("/{simulation_id}/walkthrough")
async def get_walkthrough(simulation_id: str, db: Session = Depends(get_db)) -> dict:
    """Return per-variant step-by-step funnel trace with drivers and Japanese explanations.

    Structure::

        {
            "variants": {
                "<variant_id>": [
                    {
                        "stage": "delivery",
                        "input_count": 10000,
                        "predicted_rate": 0.95,
                        "ci_lower": 0.93,
                        "ci_upper": 0.97,
                        "output_count": 9500,
                        "top_drivers": [ ... ],
                        "explanation_ja": "..."
                    },
                    ...
                ]
            }
        }
    """
    result = _require_completed_result(simulation_id, db)

    _stage_explanations: dict[str, str] = {
        FunnelStage.DELIVERY.value: (
            "配信ステージ: {input_count:,}人中{output_count:,}人に配信成功"
            " (配信率 {rate_pct:.1f}%)"
        ),
        FunnelStage.OPEN.value: (
            "開封ステージ: {input_count:,}人中{output_count:,}人が開封"
            " (開封率 {rate_pct:.1f}%)"
        ),
        FunnelStage.CLICK.value: (
            "クリックステージ: {input_count:,}人中{output_count:,}人がクリック"
            " (クリック率 {rate_pct:.1f}%)"
        ),
        FunnelStage.CONVERSION.value: (
            "コンバージョンステージ: {input_count:,}人中{output_count:,}人が転換"
            " (転換率 {rate_pct:.1f}%)"
        ),
    }

    variants_data: dict[str, list[dict]] = {}

    for variant in result.variants:
        steps: list[dict] = []

        for step in variant.funnel_steps:
            template = _stage_explanations.get(step.stage.value, "")
            explanation_ja = template.format(
                input_count=step.input_count,
                output_count=step.output_count,
                rate_pct=step.predicted_rate * 100,
            )

            drivers = [
                {
                    "feature_name": d.feature_name,
                    "label_ja": d.feature_label_ja,
                    "shap_value": d.shap_value,
                    "direction": d.direction.value,
                    "description_ja": d.description_ja,
                }
                for d in step.top_drivers
            ]

            steps.append(
                {
                    "stage": step.stage.value,
                    "input_count": step.input_count,
                    "predicted_rate": step.predicted_rate,
                    "ci_lower": step.ci_lower,
                    "ci_upper": step.ci_upper,
                    "output_count": step.output_count,
                    "top_drivers": drivers,
                    "explanation_ja": explanation_ja,
                }
            )

        variants_data[variant.variant_id] = steps

    return {"variants": variants_data}
