"""Monte Carlo simulation runner for marketing funnel analysis.

Repeatedly samples stochastic outcomes at each funnel stage to produce
robust mean estimates and confidence intervals for variant-level metrics.
"""

from typing import Callable, Optional

import numpy as np


# Default BAU control variant — no offer, plain text, afternoon send.
_CONTROL_VARIANT: dict = {
    "name": "Control (BAU)",
    "creative_id": "control_bau",
    "message_type": "text",
    "cta_type": "none",
    "offer_value": None,
    "offer_type": None,
    "image_present": False,
    "send_timing": "afternoon",
}


class MonteCarloRunner:
    """Run Monte Carlo simulations of the marketing funnel.

    For each simulation run the runner:

    1. Predicts stage-level probabilities for every agent via the
       supplied *response_model*.
    2. Samples Bernoulli outcomes at each funnel stage (delivery, open,
       click, conversion).
    3. Aggregates across agents to obtain variant-level rates.
    4. Repeats *n_simulations* times.
    5. Computes means and 95 % confidence intervals from the resulting
       distribution.
    """

    FUNNEL_STAGES = ("delivery", "open", "click", "conversion")

    def __init__(
        self,
        response_model: object,
        n_simulations: int = 1000,
        seed: int = 42,
    ) -> None:
        """Initialise the runner.

        Args:
            response_model: Any object exposing a ``predict(agent, variant)``
                method that returns a dict of ``{stage: probability}`` for
                each funnel stage.
            n_simulations: Number of Monte Carlo iterations.
            seed: Random seed for reproducibility.
        """
        self.response_model = response_model
        self.n_simulations = n_simulations
        self.rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        agents: list[dict],
        variants: list[dict],
        include_control: bool = True,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> dict:
        """Run the full Monte Carlo simulation.

        Args:
            agents: List of agent attribute dicts (one per synthetic person).
            variants: List of variant configuration dicts.
            include_control: Whether to prepend a BAU control arm.
            progress_callback: Called with a float in ``[0, 1]`` to report
                progress to the caller.

        Returns:
            Dict keyed by ``creative_id`` with per-variant results::

                {
                    "creative_id": {
                        "name": str,
                        "funnel_rates": {
                            stage: {"mean": float, "ci_lower": float, "ci_upper": float}
                        },
                        "overall_conversion": {"mean": float, "ci_lower": float, "ci_upper": float},
                        "tau_sim": float | None,
                        "predicted_rank": int,
                    }
                }
        """
        all_variants = list(variants)
        if include_control:
            all_variants.insert(0, _CONTROL_VARIANT.copy())

        total_work = len(all_variants) * self.n_simulations
        completed = 0

        variant_results: dict[str, dict] = {}

        for variant in all_variants:
            creative_id = variant.get("creative_id", variant.get("name", "unnamed"))
            stage_distributions: dict[str, list[float]] = {
                stage: [] for stage in self.FUNNEL_STAGES
            }

            for _ in range(self.n_simulations):
                run_rates = self._simulate_single_run(agents, variant)
                for stage in self.FUNNEL_STAGES:
                    stage_distributions[stage].append(run_rates[stage])

                completed += 1
                if progress_callback is not None:
                    progress_callback(completed / total_work)

            funnel_rates: dict[str, dict] = {}
            for stage in self.FUNNEL_STAGES:
                values = np.array(stage_distributions[stage])
                mean = float(np.mean(values))
                ci_lower, ci_upper = self._compute_ci(values)
                funnel_rates[stage] = {
                    "mean": mean,
                    "ci_lower": ci_lower,
                    "ci_upper": ci_upper,
                }

            conversion_values = np.array(stage_distributions["conversion"])
            conv_mean = float(np.mean(conversion_values))
            conv_lower, conv_upper = self._compute_ci(conversion_values)

            variant_results[creative_id] = {
                "name": variant.get("name", creative_id),
                "funnel_rates": funnel_rates,
                "overall_conversion": {
                    "mean": conv_mean,
                    "ci_lower": conv_lower,
                    "ci_upper": conv_upper,
                },
                "tau_sim": None,
                "predicted_rank": 0,
            }

        # Compute treatment effects relative to control
        control_id = _CONTROL_VARIANT["creative_id"]
        if include_control and control_id in variant_results:
            control_mean = variant_results[control_id]["overall_conversion"]["mean"]
            for cid, result in variant_results.items():
                if cid != control_id:
                    result["tau_sim"] = result["overall_conversion"]["mean"] - control_mean

        # Rank variants by mean conversion (1 = best)
        ranked = sorted(
            variant_results.items(),
            key=lambda item: item[1]["overall_conversion"]["mean"],
            reverse=True,
        )
        for rank, (cid, _) in enumerate(ranked, start=1):
            variant_results[cid]["predicted_rank"] = rank

        return variant_results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _simulate_single_run(self, agents: list[dict], variant: dict) -> dict:
        """Run one simulation pass through the funnel for all agents.

        For every agent the response model predicts stage probabilities,
        then Bernoulli sampling determines whether the agent progresses.
        An agent that fails at stage *k* automatically fails all
        subsequent stages (sequential funnel).

        Args:
            agents: List of agent attribute dicts.
            variant: Single variant configuration dict.

        Returns:
            Dict of ``{stage: aggregate_rate}`` across all agents.
        """
        n_agents = len(agents)
        if n_agents == 0:
            return {stage: 0.0 for stage in self.FUNNEL_STAGES}

        stage_counts: dict[str, int] = {stage: 0 for stage in self.FUNNEL_STAGES}

        for agent in agents:
            probabilities = self.response_model.predict(agent, variant)
            passed = True

            for stage in self.FUNNEL_STAGES:
                if not passed:
                    break
                prob = probabilities.get(stage, 0.0)
                outcome = self.rng.random() < prob
                if outcome:
                    stage_counts[stage] += 1
                else:
                    passed = False

        return {
            stage: stage_counts[stage] / n_agents for stage in self.FUNNEL_STAGES
        }

    @staticmethod
    def _compute_ci(
        values: np.ndarray, confidence: float = 0.95
    ) -> tuple[float, float]:
        """Compute a confidence interval using the percentile method.

        Args:
            values: 1-D array of simulation outcomes.
            confidence: Confidence level (default ``0.95`` for 95 % CI).

        Returns:
            ``(ci_lower, ci_upper)`` tuple.
        """
        alpha = 1.0 - confidence
        lower = float(np.percentile(values, 100 * alpha / 2))
        upper = float(np.percentile(values, 100 * (1 - alpha / 2)))
        return lower, upper
