"""Feature importance computation for the logistic funnel response model.

For a linear-in-logit model, the contribution of each feature to the
prediction is exactly coefficient * feature_value. This gives us
interpretable, exact feature attributions without needing SHAP's
model-agnostic approximation.
"""

from __future__ import annotations

from typing import Any

from .labels import get_description, get_label


class ShapExplainer:
    """Compute feature importance for the funnel response model."""

    def __init__(self, response_model: Any) -> None:
        """Initialize with a FunnelResponseModel instance."""
        self.response_model = response_model

    def explain_stage(
        self,
        stage: str,
        agent: dict[str, Any],
        variant: dict[str, Any],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Compute feature contributions for a single funnel stage.

        Returns a list of dicts sorted by absolute contribution, each with:
        - feature_name: str
        - feature_label_ja: str
        - shap_value: float (coefficient * feature_value)
        - direction: "positive" or "negative"
        - description_ja: str
        """
        features = self.response_model.get_feature_vector(stage, agent, variant)
        params = self.response_model.params[stage]
        coefficients = params["coefficients"]

        contributions: list[dict[str, Any]] = []
        for name, value in features.items():
            coef = coefficients.get(name, 0.0)
            contribution = coef * value
            if abs(contribution) < 1e-10:
                continue
            contributions.append(
                {
                    "feature_name": name,
                    "feature_label_ja": get_label(name),
                    "shap_value": round(contribution, 6),
                    "direction": "positive" if contribution > 0 else "negative",
                    "description_ja": get_description(name),
                }
            )

        # Sort by absolute contribution, descending
        contributions.sort(key=lambda x: abs(x["shap_value"]), reverse=True)
        return contributions[:top_k]

    def explain_variant(
        self,
        agents: list[dict[str, Any]],
        variant: dict[str, Any],
        top_k: int = 8,
    ) -> dict[str, list[dict[str, Any]]]:
        """Compute aggregated feature importance across all agents for a variant.

        Averages the feature contributions across the agent population for
        each funnel stage, giving a population-level explanation.

        Returns dict keyed by stage with list of top_k drivers per stage,
        plus an "overall" key with combined importance.
        """
        stages = ("delivery", "open", "click", "conversion")
        result: dict[str, list[dict[str, Any]]] = {}
        overall_contributions: dict[str, list[float]] = {}

        for stage in stages:
            stage_totals: dict[str, float] = {}

            for agent in agents:
                features = self.response_model.get_feature_vector(
                    stage, agent, variant
                )
                params = self.response_model.params[stage]
                coefficients = params["coefficients"]

                for name, value in features.items():
                    coef = coefficients.get(name, 0.0)
                    contribution = coef * value
                    stage_totals[name] = stage_totals.get(name, 0.0) + contribution
                    overall_contributions.setdefault(name, []).append(contribution)

            n = len(agents) if agents else 1
            stage_drivers: list[dict[str, Any]] = []
            for name, total in stage_totals.items():
                avg = total / n
                if abs(avg) < 1e-10:
                    continue
                stage_drivers.append(
                    {
                        "feature_name": name,
                        "feature_label_ja": get_label(name),
                        "shap_value": round(avg, 6),
                        "direction": "positive" if avg > 0 else "negative",
                        "description_ja": get_description(name),
                    }
                )

            stage_drivers.sort(key=lambda x: abs(x["shap_value"]), reverse=True)
            result[stage] = stage_drivers[:top_k]

        # Overall: average absolute contribution across all stages
        overall_drivers: list[dict[str, Any]] = []
        for name, values in overall_contributions.items():
            avg = sum(abs(v) for v in values) / len(values) if values else 0.0
            mean_val = sum(values) / len(values) if values else 0.0
            if abs(avg) < 1e-10:
                continue
            overall_drivers.append(
                {
                    "feature_name": name,
                    "feature_label_ja": get_label(name),
                    "shap_value": round(avg, 6),
                    "direction": "positive" if mean_val > 0 else "negative",
                    "description_ja": get_description(name),
                }
            )
        overall_drivers.sort(key=lambda x: abs(x["shap_value"]), reverse=True)
        result["overall"] = overall_drivers[:top_k]

        return result
