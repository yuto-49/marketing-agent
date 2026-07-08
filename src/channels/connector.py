"""Channel connector protocol — the interface all adapters implement.

Per the spec: one ChannelConnector interface; per-client adapter chosen
by config; capability negotiation so the orchestrator degrades gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Capabilities:
    """What the connected channel supports."""

    audiences: bool = False
    per_user_tags: bool = False
    native_conversion: bool = False
    narrowcast_ab: bool = False
    scheduling: bool = False


@dataclass(frozen=True)
class AudienceRef:
    """Opaque reference to a created audience."""

    audience_group_id: str
    size: int
    excluded_no_consent: int = 0
    excluded_freq_capped: int = 0


@dataclass(frozen=True)
class SendRef:
    """Reference to a sent/scheduled message."""

    request_id: str
    status: str  # "accepted" | "rejected"
    reason: str = ""
    projected_recipients: int = 0


@dataclass(frozen=True)
class DeliveryStats:
    """Aggregated delivery statistics."""

    requested: int = 0
    delivered: int = 0
    failed: int = 0
    opens: int = 0
    clicks: int = 0
    status: str = "unknown"  # "running" | "done" | "failed"


@dataclass(frozen=True)
class ConversionArm:
    """Conversion data for a single experiment arm."""

    arm: str
    n: int = 0
    converted: int = 0
    attribution: str = "unknown"


@dataclass(frozen=True)
class ConversionData:
    """Conversion results across arms."""

    by_arm: list[ConversionArm] = field(default_factory=list)


class ChannelConnector(Protocol):
    """Protocol for channel adapters (LINE-direct, Lステップ, Salesforce MC)."""

    def capabilities(self) -> Capabilities: ...

    def create_audience(
        self,
        description: str,
        audiences: list[str],
    ) -> AudienceRef: ...

    def send(
        self,
        audience_ref: AudienceRef,
        messages: list[dict],
        idempotency_key: str | None = None,
    ) -> SendRef: ...

    def get_stats(self, request_id: str) -> DeliveryStats: ...

    def pause(self, request_id: str) -> bool: ...
