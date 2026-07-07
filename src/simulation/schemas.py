"""Pydantic v2 schemas for the MiroFish LINE marketing campaign simulation engine.

Defines input configs, agent/population attributes, and funnel output models
for predicting delivery -> open -> click -> conversion outcomes across message variants.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MessageType(str, Enum):
    """LINE message format."""

    TEXT = "text"
    IMAGE = "image"
    RICH_MESSAGE = "rich_message"
    VIDEO = "video"


class CtaType(str, Enum):
    """Call-to-action type attached to the message."""

    URL = "url"
    COUPON = "coupon"
    SURVEY = "survey"
    NONE = "none"


class OfferType(str, Enum):
    """Category of the promotional offer."""

    DISCOUNT = "discount"
    COUPON = "coupon"
    FREE_TRIAL = "free_trial"
    INFORMATION = "information"


class SendTiming(str, Enum):
    """Time-of-day bucket for message delivery."""

    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


class AgeBand(str, Enum):
    """Age cohort of the simulated agent."""

    AGE_18_24 = "18-24"
    AGE_25_34 = "25-34"
    AGE_35_44 = "35-44"
    AGE_45_54 = "45-54"
    AGE_55_64 = "55-64"
    AGE_65_PLUS = "65+"


class Gender(str, Enum):
    """Gender attribute for the simulated agent."""

    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class EngagementLevel(str, Enum):
    """Historical engagement tier."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    DORMANT = "dormant"


class FunnelStage(str, Enum):
    """Stage within the marketing funnel."""

    DELIVERY = "delivery"
    OPEN = "open"
    CLICK = "click"
    CONVERSION = "conversion"


class DriverDirection(str, Enum):
    """Direction of a SHAP feature contribution."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


class SimulationStatus(str, Enum):
    """Lifecycle status of a simulation run."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ConfidenceLevel(str, Enum):
    """Qualitative confidence based on CI width relative to effect size."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


class VariantConfig(BaseModel):
    """Configuration for a single message variant to simulate."""

    creative_id: str = Field(..., description="Unique identifier for the creative asset")
    name: str = Field(..., description="Human-readable variant name")
    message_type: MessageType = Field(..., description="LINE message format")
    cta_type: CtaType = Field(..., description="Call-to-action type")
    offer_value: Optional[float] = Field(
        None,
        description="Monetary value of the offer in JPY",
        ge=0,
    )
    offer_type: Optional[OfferType] = Field(None, description="Category of the promotional offer")
    image_present: bool = Field(..., description="Whether the message includes an image")
    send_timing: SendTiming = Field(..., description="Time-of-day bucket for delivery")

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _offer_value_requires_type(self) -> "VariantConfig":
        """Ensure offer_value and offer_type are provided together."""
        if self.offer_value is not None and self.offer_type is None:
            raise ValueError("offer_type is required when offer_value is set")
        if self.offer_type is not None and self.offer_value is None:
            raise ValueError("offer_value is required when offer_type is set")
        return self


class SendConfig(BaseModel):
    """Delivery-window and timezone settings."""

    quiet_hours_start: str = Field(
        "22:00",
        description="Start of quiet hours in HH:MM format",
    )
    quiet_hours_end: str = Field(
        "08:00",
        description="End of quiet hours in HH:MM format",
    )
    timezone: str = Field("Asia/Tokyo", description="IANA timezone identifier")

    model_config = {"frozen": True}

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def _validate_time_format(cls, v: str) -> str:
        """Validate HH:MM time format."""
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"Expected HH:MM format, got '{v}'")
        hour, minute = parts
        if not (hour.isdigit() and minute.isdigit()):
            raise ValueError(f"Expected HH:MM format with digits, got '{v}'")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError(f"Invalid time '{v}': hour must be 0-23, minute 0-59")
        return v


class SimulationInput(BaseModel):
    """Top-level input for a simulation run."""

    segment_id: str = Field(..., description="Target audience segment identifier")
    variants: list[VariantConfig] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Message variants to evaluate (1-5)",
    )
    send_config: SendConfig = Field(
        default_factory=SendConfig,
        description="Delivery-window configuration",
    )
    n_simulations: int = Field(
        1000,
        ge=100,
        le=10000,
        description="Number of Monte Carlo simulation iterations",
    )
    include_control: bool = Field(
        True,
        description="Whether to include a no-message control group",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Agent / Population models
# ---------------------------------------------------------------------------


class AgentAttributes(BaseModel):
    """Demographic and behavioral attributes of a simulated LINE friend."""

    age_band: AgeBand = Field(..., description="Age cohort")
    gender: Gender = Field(..., description="Gender attribute")
    prefecture: str = Field(..., description="Japanese prefecture of residence")
    days_since_friend_add: int = Field(
        ...,
        ge=0,
        description="Days elapsed since the user added the LINE official account",
    )
    past_open_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Historical message open rate (0-1)",
    )
    past_click_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Historical click-through rate (0-1)",
    )
    engagement_level: EngagementLevel = Field(..., description="Engagement tier")
    tag_set: list[str] = Field(
        default_factory=list,
        description="CRM tags assigned to this user",
    )

    model_config = {"frozen": True}

    @field_validator("past_click_rate")
    @classmethod
    def _click_rate_le_open_rate(cls, v: float, info) -> float:
        """Click rate should not exceed open rate."""
        open_rate = info.data.get("past_open_rate")
        if open_rate is not None and v > open_rate:
            raise ValueError(
                f"past_click_rate ({v}) must not exceed past_open_rate ({open_rate})"
            )
        return v


# ---------------------------------------------------------------------------
# Funnel output models
# ---------------------------------------------------------------------------


class FunnelStageResult(BaseModel):
    """Predicted outcome for a single funnel stage."""

    stage: FunnelStage = Field(..., description="Funnel stage identifier")
    input_count: int = Field(..., ge=0, description="Number of users entering this stage")
    predicted_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Point estimate of the stage conversion rate",
    )
    ci_lower: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Lower bound of the 95% confidence interval",
    )
    ci_upper: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Upper bound of the 95% confidence interval",
    )
    output_count: int = Field(..., ge=0, description="Number of users proceeding to the next stage")

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _ci_ordering(self) -> "FunnelStageResult":
        """Ensure CI lower <= predicted_rate <= CI upper."""
        if self.ci_lower > self.predicted_rate:
            raise ValueError(
                f"ci_lower ({self.ci_lower}) must not exceed predicted_rate ({self.predicted_rate})"
            )
        if self.predicted_rate > self.ci_upper:
            raise ValueError(
                f"predicted_rate ({self.predicted_rate}) must not exceed ci_upper ({self.ci_upper})"
            )
        return self

    @model_validator(mode="after")
    def _output_le_input(self) -> "FunnelStageResult":
        """Output count must not exceed input count."""
        if self.output_count > self.input_count:
            raise ValueError(
                f"output_count ({self.output_count}) must not exceed input_count ({self.input_count})"
            )
        return self


class DriverContribution(BaseModel):
    """SHAP-based feature contribution explaining a funnel stage prediction."""

    feature_name: str = Field(..., description="Machine-readable feature identifier")
    feature_label_ja: str = Field(..., description="Japanese display label for the feature")
    shap_value: float = Field(..., description="SHAP contribution value")
    direction: DriverDirection = Field(..., description="Direction of impact on the prediction")
    description_ja: str = Field(..., description="Japanese explanation of this driver's effect")

    model_config = {"frozen": True}


class FunnelStepTrace(BaseModel):
    """A funnel stage result enriched with explainability drivers."""

    stage: FunnelStage = Field(..., description="Funnel stage identifier")
    input_count: int = Field(..., ge=0, description="Number of users entering this stage")
    predicted_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Point estimate of the stage conversion rate",
    )
    ci_lower: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Lower bound of the 95% confidence interval",
    )
    ci_upper: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Upper bound of the 95% confidence interval",
    )
    output_count: int = Field(..., ge=0, description="Number of users proceeding to the next stage")
    top_drivers: list[DriverContribution] = Field(
        default_factory=list,
        description="Top SHAP-based drivers explaining this stage's prediction",
    )

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _ci_ordering(self) -> "FunnelStepTrace":
        """Ensure CI lower <= predicted_rate <= CI upper."""
        if self.ci_lower > self.predicted_rate:
            raise ValueError(
                f"ci_lower ({self.ci_lower}) must not exceed predicted_rate ({self.predicted_rate})"
            )
        if self.predicted_rate > self.ci_upper:
            raise ValueError(
                f"predicted_rate ({self.predicted_rate}) must not exceed ci_upper ({self.ci_upper})"
            )
        return self

    @model_validator(mode="after")
    def _output_le_input(self) -> "FunnelStepTrace":
        """Output count must not exceed input count."""
        if self.output_count > self.input_count:
            raise ValueError(
                f"output_count ({self.output_count}) must not exceed input_count ({self.input_count})"
            )
        return self


class VariantResult(BaseModel):
    """Simulation results for a single message variant."""

    variant_id: str = Field(..., description="Identifier matching the input creative_id")
    variant_name: str = Field(..., description="Human-readable variant name")
    funnel_steps: list[FunnelStepTrace] = Field(
        ...,
        description="Ordered funnel stage traces (delivery -> open -> click -> conversion)",
    )
    overall_conversion_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="End-to-end conversion rate across all funnel stages",
    )
    conversion_ci: tuple[float, float] = Field(
        ...,
        description="95% confidence interval for the overall conversion rate (lower, upper)",
    )
    tau_sim: float = Field(
        ...,
        description="Estimated treatment effect vs. control group",
    )
    predicted_rank: int = Field(
        ...,
        ge=1,
        description="Rank among variants (1 = best predicted performer)",
    )

    model_config = {"frozen": True}

    @field_validator("conversion_ci")
    @classmethod
    def _ci_tuple_ordering(cls, v: tuple[float, float]) -> tuple[float, float]:
        """Lower bound must not exceed upper bound."""
        lower, upper = v
        if lower < 0.0 or upper > 1.0:
            raise ValueError(f"CI bounds must be in [0, 1], got ({lower}, {upper})")
        if lower > upper:
            raise ValueError(f"CI lower ({lower}) must not exceed upper ({upper})")
        return v


class SimulationResult(BaseModel):
    """Complete output of a simulation run across all variants."""

    simulation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique simulation run identifier (UUID)",
    )
    segment_id: str = Field(..., description="Target audience segment identifier")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the simulation was created",
    )
    status: SimulationStatus = Field(..., description="Current lifecycle status")
    variants: list[VariantResult] = Field(
        ...,
        description="Per-variant simulation results",
    )
    recommended_variant_id: str = Field(
        ...,
        description="Variant ID with the best predicted outcome",
    )
    confidence_level: ConfidenceLevel = Field(
        ...,
        description="Qualitative confidence based on CI width vs. effect size",
    )
    executive_summary_ja: Optional[str] = Field(
        None,
        description="Japanese executive summary of the simulation findings",
    )
    progress_pct: Optional[float] = Field(
        None,
        ge=0.0,
        le=100.0,
        description="Simulation progress percentage (0-100)",
    )

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _recommended_variant_exists(self) -> "SimulationResult":
        """Ensure recommended_variant_id refers to an actual variant."""
        variant_ids = {v.variant_id for v in self.variants}
        if self.recommended_variant_id not in variant_ids:
            raise ValueError(
                f"recommended_variant_id '{self.recommended_variant_id}' "
                f"not found in variants: {variant_ids}"
            )
        return self
