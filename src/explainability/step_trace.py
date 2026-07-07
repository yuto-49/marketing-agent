"""Build enriched step-by-step traces for the simulation walkthrough view."""

from __future__ import annotations

from typing import Any

from .shap_explainer import ShapExplainer


class StepTraceBuilder:
    """Enrich funnel step traces with explainability drivers.

    Takes a SimulationResult and adds driver contribution data to each
    FunnelStepTrace, producing the data needed for the step-by-step
    walkthrough UI view.
    """

    def __init__(self, shap_explainer: ShapExplainer, response_model: Any) -> None:
        self.shap_explainer = shap_explainer
        self.response_model = response_model

    def build_enriched_traces(
        self,
        agents: list[dict[str, Any]],
        variant: dict[str, Any],
        funnel_steps: list[Any],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Build enriched trace data for each funnel step.

        For each funnel stage, computes the average feature contributions
        across the agent population and attaches them as top_drivers.

        Returns list of dicts, one per funnel stage, each containing:
        - stage: str
        - input_count: int
        - predicted_rate: float
        - ci_lower: float
        - ci_upper: float
        - output_count: int
        - top_drivers: list[dict] (from ShapExplainer)
        - explanation_ja: str (generated Japanese explanation)
        """
        variant_explanation = self.shap_explainer.explain_variant(
            agents, variant, top_k=top_k
        )

        enriched: list[dict[str, Any]] = []
        for step in funnel_steps:
            stage_name = (
                step.stage if isinstance(step.stage, str) else step.stage.value
            )
            drivers = variant_explanation.get(stage_name, [])

            explanation = self._generate_stage_explanation(stage_name, step, drivers)

            enriched.append(
                {
                    "stage": stage_name,
                    "input_count": step.input_count,
                    "predicted_rate": step.predicted_rate,
                    "ci_lower": step.ci_lower,
                    "ci_upper": step.ci_upper,
                    "output_count": step.output_count,
                    "top_drivers": drivers[:top_k],
                    "explanation_ja": explanation,
                }
            )

        return enriched

    def _generate_stage_explanation(
        self,
        stage: str,
        step: Any,
        drivers: list[dict[str, Any]],
    ) -> str:
        """Generate a Japanese explanation paragraph for a funnel stage."""
        stage_names_ja = {
            "delivery": "配信",
            "open": "開封",
            "click": "クリック",
            "conversion": "転換",
        }
        stage_ja = stage_names_ja.get(stage, stage)
        rate_pct = step.predicted_rate * 100

        explanation = f"{stage_ja}率は{rate_pct:.1f}%と予測されます。"

        if drivers:
            top = drivers[0]
            label = top["feature_label_ja"]
            direction = "プラス" if top["direction"] == "positive" else "マイナス"
            explanation += f"主な要因は「{label}」で、{direction}の影響を与えています。"

            if len(drivers) > 1:
                second = drivers[1]
                explanation += (
                    f"次いで「{second['feature_label_ja']}」も影響しています。"
                )

        ci_lower_pct = step.ci_lower * 100
        ci_upper_pct = step.ci_upper * 100
        explanation += f"（95%信頼区間: {ci_lower_pct:.1f}%〜{ci_upper_pct:.1f}%）"

        return explanation
