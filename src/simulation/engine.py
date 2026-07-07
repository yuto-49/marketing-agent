"""Main simulation engine that orchestrates the full MiroFish pipeline.

Chains together: segment parameter loading, synthetic population generation,
funnel response modelling, Monte Carlo simulation, and result assembly.

Usage::

    from pathlib import Path
    from simulation import SimulationEngine, SimulationInput

    engine = SimulationEngine(params_dir=Path("src/data/segment_params"))
    result = engine.run(SimulationInput(...))
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .params import SegmentParamsLoader
from .population import SyntheticPopulation
from .response_model import FunnelResponseModel
from .runner import MonteCarloRunner
from .schemas import (
    ConfidenceLevel,
    FunnelStepTrace,
    SimulationInput,
    SimulationResult,
    SimulationStatus,
    VariantResult,
)

# Population size used when generating synthetic agents.
_DEFAULT_POPULATION_SIZE = 10_000


class SimulationEngine:
    """Main simulation engine that orchestrates the full pipeline.

    Typical lifecycle:

    1. Instantiate with a *params_dir* pointing at segment JSON files.
    2. Call :meth:`run` with a :class:`SimulationInput` to execute the
       simulation and receive a :class:`SimulationResult`.

    Example::

        engine = SimulationEngine(params_dir=Path("src/data/segment_params"))
        result = engine.run(SimulationInput(...))
    """

    def __init__(
        self,
        params_dir: Path,
        model_params: Optional[dict[str, Any]] = None,
        population_size: int = _DEFAULT_POPULATION_SIZE,
    ) -> None:
        """Initialise with params directory and optional custom model parameters.

        Args:
            params_dir: Directory containing segment parameter JSON files.
            model_params: Optional parameter dict passed to
                :class:`FunnelResponseModel`.  When *None* the model's
                built-in defaults are used.
            population_size: Number of synthetic agents to generate per run.
        """
        self.params_loader = SegmentParamsLoader(params_dir)
        self.population_size = population_size

        if model_params is not None:
            self.response_model = FunnelResponseModel(model_params)
        else:
            self.response_model = FunnelResponseModel.with_defaults()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        sim_input: SimulationInput,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> SimulationResult:
        """Run a complete simulation.

        Steps:

        1. Locate the segment parameter file.
        2. Generate a synthetic agent population.
        3. Convert variant configs to plain dicts for the runner.
        4. Run Monte Carlo simulation across all variants.
        5. Assemble :class:`VariantResult` objects with funnel traces.
        6. Determine the recommended variant and confidence level.

        Args:
            sim_input: Fully specified simulation input.
            progress_callback: Optional callable receiving a float in
                ``[0, 1]`` to report progress.

        Returns:
            A :class:`SimulationResult` containing per-variant metrics,
            the recommended variant, and the confidence level.
        """
        simulation_id = str(uuid.uuid4())

        # 1. Find the segment param file path and build population
        param_file = self._find_param_file(sim_input.segment_id)
        population = SyntheticPopulation(param_file)
        agents = population.generate(self.population_size)

        # 2. Convert VariantConfig objects to plain dicts for the runner
        variant_dicts = [
            {
                "creative_id": v.creative_id,
                "name": v.name,
                "message_type": v.message_type.value,
                "cta_type": v.cta_type.value,
                "offer_value": v.offer_value,
                "offer_type": v.offer_type.value if v.offer_type else None,
                "image_present": v.image_present,
                "send_timing": v.send_timing.value,
            }
            for v in sim_input.variants
        ]

        # 3. Run Monte Carlo
        runner = MonteCarloRunner(
            response_model=self.response_model,
            n_simulations=sim_input.n_simulations,
        )
        raw_results = runner.run(
            agents=agents,
            variants=variant_dicts,
            include_control=sim_input.include_control,
            progress_callback=progress_callback,
        )

        # 4. Assemble VariantResult objects
        n_agents = len(agents)
        variant_results: list[VariantResult] = []

        for creative_id, data in raw_results.items():
            funnel_steps = self._build_funnel_steps(data["funnel_rates"], n_agents)
            conv = data["overall_conversion"]

            variant_results.append(
                VariantResult(
                    variant_id=creative_id,
                    variant_name=data["name"],
                    funnel_steps=funnel_steps,
                    overall_conversion_rate=conv["mean"],
                    conversion_ci=(conv["ci_lower"], conv["ci_upper"]),
                    tau_sim=data["tau_sim"] if data["tau_sim"] is not None else 0.0,
                    predicted_rank=data["predicted_rank"],
                )
            )

        # 5. Determine recommended variant (best non-control)
        variant_results.sort(key=lambda v: v.predicted_rank)
        recommended = next(
            (v for v in variant_results if v.variant_id != "control_bau"),
            variant_results[0] if variant_results else None,
        )

        confidence_level = self._determine_confidence(variant_results)

        return SimulationResult(
            simulation_id=simulation_id,
            segment_id=sim_input.segment_id,
            status=SimulationStatus.COMPLETED,
            variants=variant_results,
            recommended_variant_id=recommended.variant_id if recommended else "",
            confidence_level=confidence_level,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_param_file(self, segment_id: str) -> Path:
        """Locate the segment parameter JSON file.

        Uses the params loader's directory and glob logic to find the file
        path, which is then passed to :class:`SyntheticPopulation`.
        """
        candidates = sorted(
            self.params_loader.params_dir.glob(f"*{segment_id}*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError(
                f"No parameter file for segment '{segment_id}' "
                f"in {self.params_loader.params_dir}"
            )
        return candidates[0]

    @staticmethod
    def _build_funnel_steps(
        funnel_rates: dict[str, dict[str, float]],
        n_agents: int,
    ) -> list[FunnelStepTrace]:
        """Convert raw runner funnel rates into schema FunnelStepTrace objects."""
        stages = ("delivery", "open", "click", "conversion")
        steps: list[FunnelStepTrace] = []
        remaining = n_agents

        for stage in stages:
            metrics = funnel_rates[stage]
            mean_rate = metrics["mean"]
            output = int(remaining * mean_rate)

            steps.append(
                FunnelStepTrace(
                    stage=stage,
                    input_count=remaining,
                    predicted_rate=round(mean_rate, 6),
                    ci_lower=round(metrics["ci_lower"], 6),
                    ci_upper=round(metrics["ci_upper"], 6),
                    output_count=output,
                )
            )
            remaining = output

        return steps

    @staticmethod
    def _determine_confidence(results: list[VariantResult]) -> ConfidenceLevel:
        """Determine confidence level based on CI width relative to effect size.

        Heuristic:

        * **high** -- effect size > 2x the CI width of the best variant.
        * **medium** -- effect size > 1x the CI width.
        * **low** -- effect size <= CI width (overlapping intervals).
        """
        treatment_results = [r for r in results if r.variant_id != "control_bau"]
        if not treatment_results:
            return ConfidenceLevel.LOW

        best = min(treatment_results, key=lambda r: r.predicted_rank)
        ci_lower, ci_upper = best.conversion_ci
        ci_width = ci_upper - ci_lower

        if ci_width <= 0:
            return ConfidenceLevel.HIGH

        effect_size = abs(best.tau_sim)

        if effect_size > 2 * ci_width:
            return ConfidenceLevel.HIGH
        if effect_size > ci_width:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW
