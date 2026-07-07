"""Logistic funnel response model for LINE marketing simulation.

Models the probability of each funnel stage (delivery, open, click, conversion)
as a logistic function of agent attributes and message variant attributes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


# Ordered funnel stages.
FUNNEL_STAGES: tuple[str, ...] = ("delivery", "open", "click", "conversion")


class FunnelResponseModel:
    """Logistic response model for LINE marketing funnel stages.

    Each stage models ``P(outcome | agent_attributes, variant_attributes)``
    using a logistic function with interpretable parameters::

        P = 1 / (1 + exp(-(intercept + sum(coef_i * x_i))))

    Parameters
    ----------
    params : dict
        Model parameters keyed by stage name.  Each stage has:
        - ``intercept``: float -- logistic intercept
        - ``coefficients``: dict[str, float] -- named coefficients
    """

    def __init__(self, params: dict[str, dict[str, Any]]) -> None:
        for stage in FUNNEL_STAGES:
            if stage not in params:
                raise KeyError(f"Missing parameters for stage: {stage}")
            if "intercept" not in params[stage]:
                raise KeyError(f"Missing intercept for stage: {stage}")
            if "coefficients" not in params[stage]:
                raise KeyError(f"Missing coefficients for stage: {stage}")
        self._params = params

    @property
    def params(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the internal parameters."""
        return {
            stage: {
                "intercept": self._params[stage]["intercept"],
                "coefficients": dict(self._params[stage]["coefficients"]),
            }
            for stage in FUNNEL_STAGES
        }

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict_stage(
        self,
        stage: str,
        agent: dict[str, Any],
        variant: dict[str, Any],
    ) -> float:
        """Predict probability for a single funnel stage.

        Args:
            stage: One of ``delivery``, ``open``, ``click``, ``conversion``.
            agent: Agent attribute dictionary.
            variant: Message variant attribute dictionary.

        Returns:
            Probability in [0, 1].
        """
        if stage not in FUNNEL_STAGES:
            raise ValueError(f"Unknown stage: {stage}. Must be one of {FUNNEL_STAGES}")
        features = self.get_feature_vector(stage, agent, variant)
        stage_params = self._params[stage]
        logit = stage_params["intercept"]
        coefficients = stage_params["coefficients"]
        for name, value in features.items():
            if name in coefficients:
                logit += coefficients[name] * value
        return _sigmoid(logit)

    def predict(
        self,
        agent: dict[str, Any],
        variant: dict[str, Any],
    ) -> dict[str, float]:
        """Predict probabilities for all funnel stages.

        This is the primary interface consumed by the Monte Carlo runner.

        Returns:
            Dictionary mapping stage name to conditional probability::

                {"delivery": 0.90, "open": 0.32, "click": 0.10, "conversion": 0.03}
        """
        return {
            stage: self.predict_stage(stage, agent, variant)
            for stage in FUNNEL_STAGES
        }

    def predict_funnel(
        self,
        agent: dict[str, Any],
        variant: dict[str, Any],
    ) -> dict[str, float]:
        """Predict all funnel stages, returning cumulative and stage-level rates.

        The chained probability is:
            P(convert) = P(deliver) * P(open|deliver) * P(click|open) * P(convert|click)

        Returns:
            Dictionary with keys for each stage rate plus ``cumulative_conversion_rate``.
        """
        probs = self.predict(agent, variant)
        cumulative = 1.0
        for stage in FUNNEL_STAGES:
            cumulative *= probs[stage]

        return {
            "delivery_rate": probs["delivery"],
            "open_rate": probs["open"],
            "click_rate": probs["click"],
            "conversion_rate": probs["conversion"],
            "cumulative_conversion_rate": cumulative,
        }

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def get_feature_vector(
        self,
        stage: str,
        agent: dict[str, Any],
        variant: dict[str, Any],
    ) -> dict[str, float]:
        """Extract a named feature vector for a given stage.

        Features are constructed from the union of agent and variant attributes
        with stage-specific transformations.  This output is also consumed by
        SHAP explainers for interpretability.

        Args:
            stage: Funnel stage name.
            agent: Agent attribute dictionary.
            variant: Message variant attribute dictionary.

        Returns:
            Dictionary mapping feature names to numeric values.
        """
        features: dict[str, float] = {}

        # --- Agent features (shared across stages) ----------------------
        days = float(agent.get("days_since_friend_add", 90))
        features["log_days_since_friend_add"] = math.log1p(days)
        features["past_open_rate"] = float(agent.get("past_open_rate", 0.25))
        features["past_click_rate"] = float(agent.get("past_click_rate", 0.10))

        # Engagement level one-hot encoding
        engagement = agent.get("engagement_level", "medium")
        features["is_high_engagement"] = 1.0 if engagement == "high" else 0.0
        features["is_medium_engagement"] = 1.0 if engagement == "medium" else 0.0
        features["is_low_engagement"] = 1.0 if engagement == "low" else 0.0
        features["is_dormant"] = 1.0 if engagement == "dormant" else 0.0

        # Age band encoding (ordinal midpoint, normalised to 0-1)
        age_midpoints = {
            "18-24": 21, "25-34": 30, "35-44": 40,
            "45-54": 50, "55-64": 60, "65+": 70,
        }
        age_band = agent.get("age_band", "35-44")
        features["age_midpoint_norm"] = age_midpoints.get(age_band, 40) / 70.0

        # --- Variant features -------------------------------------------
        # Message type (matches VariantConfig.message_type / dict key)
        msg_type = variant.get("message_type", "text")
        features["is_rich_message"] = 1.0 if msg_type in ("rich_message", "image") else 0.0
        features["is_image"] = 1.0 if msg_type == "image" else 0.0

        # Send timing (matches VariantConfig.send_timing enum values)
        send_timing = variant.get("send_timing", "afternoon")
        features["is_evening"] = 1.0 if send_timing == "evening" else 0.0
        features["is_morning"] = 1.0 if send_timing == "morning" else 0.0

        # CTA type
        cta = variant.get("cta_type", "link")
        features["is_coupon_cta"] = 1.0 if cta == "coupon" else 0.0

        # Offer value (log-scaled, in JPY; default 0 means no offer)
        raw_offer = variant.get("offer_value")
        offer_value = float(raw_offer) if raw_offer is not None else 0.0
        features["log_offer_value"] = math.log1p(offer_value)

        # Offer type
        offer_type = variant.get("offer_type", "none")
        features["is_discount"] = 1.0 if offer_type == "discount" else 0.0
        features["is_coupon_offer"] = 1.0 if offer_type == "coupon" else 0.0

        return features

    # ------------------------------------------------------------------
    # Default calibrated parameters
    # ------------------------------------------------------------------

    @classmethod
    def default_params(cls) -> dict[str, dict[str, Any]]:
        """Return default model parameters calibrated to typical LINE campaign performance.

        Typical LINE OA metrics (Japan market):
        - Delivery rate: 85-95 % (depends on block rate)
        - Open rate: 20-40 % (rich messages higher)
        - Click rate: 5-15 % of opens
        - Conversion rate: 1-5 % of clicks

        The intercepts and coefficients are tuned so that an *average* agent
        with a standard text message yields rates near the middle of these
        ranges.
        """
        return {
            # ----- Delivery stage ----------------------------------------
            # Baseline ~90 % for an average agent.
            # Dormant / long-tenure friends are more likely to have blocked.
            "delivery": {
                "intercept": 2.2,
                "coefficients": {
                    "log_days_since_friend_add": -0.15,
                    "is_dormant": -1.0,
                    "is_low_engagement": -0.3,
                    "is_high_engagement": 0.2,
                    "past_open_rate": 0.5,
                },
            },
            # ----- Open stage --------------------------------------------
            # Baseline ~30 % for a text message to a medium-engagement user.
            # Rich messages, evening sends, and high engagement boost opens.
            "open": {
                "intercept": -0.85,
                "coefficients": {
                    "log_days_since_friend_add": -0.08,
                    "is_rich_message": 0.35,
                    "is_image": 0.20,
                    "is_evening": 0.25,
                    "is_morning": 0.15,
                    "is_high_engagement": 0.60,
                    "is_medium_engagement": 0.10,
                    "is_dormant": -0.80,
                    "past_open_rate": 1.50,
                    "age_midpoint_norm": -0.10,
                },
            },
            # ----- Click stage -------------------------------------------
            # Baseline ~10 % of opens.
            # Offer value, coupon CTA, and past click behaviour drive clicks.
            "click": {
                "intercept": -2.2,
                "coefficients": {
                    "log_offer_value": 0.12,
                    "is_coupon_cta": 0.45,
                    "past_click_rate": 2.00,
                    "is_high_engagement": 0.40,
                    "is_dormant": -0.50,
                    "is_rich_message": 0.20,
                    "log_days_since_friend_add": -0.05,
                },
            },
            # ----- Conversion stage --------------------------------------
            # Baseline ~3 % of clicks.
            # High offer value, discount/coupon offers, and engaged users
            # convert better.  Long-tenure dormant users convert poorly.
            "conversion": {
                "intercept": -3.5,
                "coefficients": {
                    "log_offer_value": 0.18,
                    "is_discount": 0.55,
                    "is_coupon_offer": 0.50,
                    "is_high_engagement": 0.50,
                    "is_medium_engagement": 0.10,
                    "is_dormant": -0.60,
                    "log_days_since_friend_add": -0.10,
                    "past_click_rate": 1.20,
                },
            },
        }

    @classmethod
    def with_defaults(cls) -> "FunnelResponseModel":
        """Convenience constructor using default calibrated parameters."""
        return cls(cls.default_params())


# ======================================================================
# Private helpers
# ======================================================================

def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid function."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)
