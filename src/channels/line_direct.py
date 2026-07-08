"""LINE-direct channel adapter using Messaging API + Insight API.

Tier A adapter (SMB, LINE OA only). Supports:
  - Audience creation via upload
  - Narrowcast messaging to audience groups
  - Delivery/insight stats polling
  - Campaign pause (no native support — marks as paused locally)

Requires LINE_CHANNEL_ACCESS_TOKEN environment variable.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx

from .connector import (
    AudienceRef,
    Capabilities,
    DeliveryStats,
    SendRef,
)

logger = logging.getLogger(__name__)

_LINE_API_BASE = "https://api.line.me/v2/bot"


class LineDirectConnector:
    """LINE Messaging API + Insight API adapter.

    Implements the ChannelConnector protocol for LINE-direct integration.
    Uses narrowcast for audience-targeted sends and the Insight API for
    delivery statistics.
    """

    def __init__(self, channel_access_token: str | None = None) -> None:
        token = channel_access_token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        if not token:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set — API calls will fail")
        self._token = token
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._paused: set[str] = set()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            audiences=True,
            per_user_tags=False,
            native_conversion=False,
            narrowcast_ab=True,
            scheduling=False,
        )

    # ------------------------------------------------------------------
    # Audience management
    # ------------------------------------------------------------------

    def create_audience(
        self,
        description: str,
        audiences: list[str],
    ) -> AudienceRef:
        """Create an audience group by uploading user IDs.

        Args:
            description: Human-readable audience name.
            audiences: List of LINE user IDs to include.

        Returns:
            AudienceRef with the created audience group ID.
        """
        payload: dict[str, Any] = {
            "description": description,
            "isIfaAudience": False,
            "audiences": [{"id": uid} for uid in audiences],
        }

        resp = self._client.post(
            f"{_LINE_API_BASE}/audienceGroup/upload",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        audience_group_id = str(data.get("audienceGroupId", ""))
        logger.info(
            "Created audience group %s (%s) with %d users",
            audience_group_id,
            description,
            len(audiences),
        )

        return AudienceRef(
            audience_group_id=audience_group_id,
            size=len(audiences),
        )

    def get_audience(self, audience_group_id: str) -> dict:
        """Get audience group details."""
        resp = self._client.get(
            f"{_LINE_API_BASE}/audienceGroup/{audience_group_id}",
        )
        resp.raise_for_status()
        return resp.json()

    def delete_audience(self, audience_group_id: str) -> bool:
        """Delete an audience group."""
        resp = self._client.delete(
            f"{_LINE_API_BASE}/audienceGroup/{audience_group_id}",
        )
        return resp.status_code == 200

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    def send(
        self,
        audience_ref: AudienceRef,
        messages: list[dict],
        idempotency_key: str | None = None,
    ) -> SendRef:
        """Send a narrowcast message to an audience group.

        Args:
            audience_ref: Target audience from create_audience.
            messages: LINE message objects (text, image, flex, etc.).
            idempotency_key: Retry key to prevent double-sends.

        Returns:
            SendRef with request ID and status.
        """
        if not idempotency_key:
            idempotency_key = str(uuid.uuid4())

        payload: dict[str, Any] = {
            "messages": messages,
            "recipient": {
                "type": "audience",
                "audienceGroupId": int(audience_ref.audience_group_id),
            },
        }

        headers = {"X-Line-Retry-Key": idempotency_key}

        resp = self._client.post(
            f"{_LINE_API_BASE}/message/narrowcast",
            json=payload,
            headers=headers,
        )

        if resp.status_code == 202:
            request_id = resp.headers.get("x-line-request-id", idempotency_key)
            logger.info(
                "Narrowcast accepted: request_id=%s, audience=%s",
                request_id,
                audience_ref.audience_group_id,
            )
            return SendRef(
                request_id=request_id,
                status="accepted",
                projected_recipients=audience_ref.size,
            )

        logger.warning("Narrowcast rejected: %s %s", resp.status_code, resp.text)
        return SendRef(
            request_id=idempotency_key,
            status="rejected",
            reason=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )

    def send_push(self, user_id: str, messages: list[dict]) -> SendRef:
        """Send a push message to a single user (for testing)."""
        payload = {"to": user_id, "messages": messages}
        resp = self._client.post(
            f"{_LINE_API_BASE}/message/push",
            json=payload,
        )

        request_id = resp.headers.get("x-line-request-id", str(uuid.uuid4()))
        if resp.status_code == 200:
            return SendRef(request_id=request_id, status="accepted", projected_recipients=1)

        return SendRef(
            request_id=request_id,
            status="rejected",
            reason=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )

    def send_multicast(self, user_ids: list[str], messages: list[dict]) -> SendRef:
        """Send a multicast message to up to 500 users."""
        payload = {"to": user_ids, "messages": messages}
        resp = self._client.post(
            f"{_LINE_API_BASE}/message/multicast",
            json=payload,
        )

        request_id = resp.headers.get("x-line-request-id", str(uuid.uuid4()))
        if resp.status_code == 200:
            return SendRef(
                request_id=request_id,
                status="accepted",
                projected_recipients=len(user_ids),
            )

        return SendRef(
            request_id=request_id,
            status="rejected",
            reason=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )

    # ------------------------------------------------------------------
    # Stats / Insight
    # ------------------------------------------------------------------

    def get_stats(self, request_id: str) -> DeliveryStats:
        """Poll narrowcast delivery progress via Insight API.

        Args:
            request_id: The x-line-request-id from the send response.

        Returns:
            DeliveryStats with current delivery counts.
        """
        resp = self._client.get(
            f"{_LINE_API_BASE}/message/progress/narrowcast",
            params={"requestId": request_id},
        )

        if resp.status_code != 200:
            logger.warning("Stats lookup failed for %s: %s", request_id, resp.status_code)
            return DeliveryStats(status="failed")

        data = resp.json()
        phase = data.get("phase", "waiting")

        status_map = {
            "waiting": "running",
            "sending": "running",
            "succeeded": "done",
            "failed": "failed",
        }

        return DeliveryStats(
            requested=data.get("targetCount", 0),
            delivered=data.get("successCount", 0),
            failed=data.get("failureCount", 0),
            opens=0,
            clicks=0,
            status=status_map.get(phase, "unknown"),
        )

    def get_message_event(self, request_id: str) -> dict:
        """Get message event data (opens, clicks) from Insight API."""
        resp = self._client.get(
            f"{_LINE_API_BASE}/insight/message/event",
            params={"requestId": request_id},
        )
        if resp.status_code == 200:
            return resp.json()
        return {}

    # ------------------------------------------------------------------
    # Campaign control
    # ------------------------------------------------------------------

    def pause(self, request_id: str) -> bool:
        """Mark a campaign as paused.

        LINE doesn't support pausing narrowcast mid-flight, so this
        is tracked locally and prevents follow-up sends.
        """
        self._paused.add(request_id)
        logger.info("Campaign %s marked as paused (local)", request_id)
        return True

    def is_paused(self, request_id: str) -> bool:
        """Check if a campaign has been paused."""
        return request_id in self._paused

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_bot_info(self) -> dict:
        """Get the bot's basic info (name, icon, etc.)."""
        resp = self._client.get(f"{_LINE_API_BASE}/info")
        if resp.status_code == 200:
            return resp.json()
        return {}

    def get_follower_count(self) -> int | None:
        """Get the number of followers (friends) for this bot."""
        resp = self._client.get(f"{_LINE_API_BASE}/insight/followers")
        if resp.status_code == 200:
            return resp.json().get("followers")
        return None

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
