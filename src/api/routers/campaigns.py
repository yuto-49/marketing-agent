"""Campaign endpoints — create, send, and monitor LINE campaigns."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.channels.line_direct import LineDirectConnector
from src.db.models import CampaignRecord

from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CampaignCreateRequest(BaseModel):
    simulation_id: str
    variant_id: str
    segment_id: str
    user_ids: list[str] = Field(..., min_length=1, description="LINE user IDs to target")
    messages: list[dict] = Field(..., min_length=1, description="LINE message objects")


class CampaignResponse(BaseModel):
    campaign_id: str
    simulation_id: str
    variant_id: str
    status: str
    audience_group_id: Optional[str] = None
    request_id: Optional[str] = None
    recipient_count: int = 0


class CampaignStatsResponse(BaseModel):
    campaign_id: str
    status: str
    requested: int = 0
    delivered: int = 0
    failed: int = 0
    opens: int = 0
    clicks: int = 0
    delivery_status: str = "unknown"


class CampaignSummary(BaseModel):
    campaign_id: str
    simulation_id: str
    variant_id: str
    segment_id: str
    status: str
    created_at: datetime
    recipient_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_line_connector() -> LineDirectConnector:
    """Create a LINE connector instance."""
    return LineDirectConnector()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=CampaignResponse, status_code=201)
async def create_campaign(
    req: CampaignCreateRequest,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Create a campaign, build the audience, and send via LINE narrowcast.

    This endpoint:
    1. Validates the simulation exists and is completed
    2. Creates an audience group on LINE with the provided user IDs
    3. Sends a narrowcast message to that audience
    4. Persists the campaign record
    """
    from src.db import repository as repo

    # Validate simulation
    sim = repo.get_simulation(db, req.simulation_id)
    if sim is None:
        raise HTTPException(status_code=404, detail="Simulation not found")
    if sim.status != "completed":
        raise HTTPException(status_code=409, detail="Simulation not yet completed")

    campaign_id = str(uuid.uuid4())
    connector = _get_line_connector()

    try:
        # Create audience
        audience_desc = f"mirofish_{campaign_id[:8]}_{req.variant_id}"
        audience_ref = connector.create_audience(audience_desc, req.user_ids)

        # Send narrowcast
        idempotency_key = f"mirofish-{campaign_id}"
        send_ref = connector.send(audience_ref, req.messages, idempotency_key)

        # Persist
        record = CampaignRecord(
            campaign_id=campaign_id,
            simulation_id=req.simulation_id,
            segment_id=req.segment_id,
            variant_id=req.variant_id,
            status="sent" if send_ref.status == "accepted" else "failed",
            sent_at=datetime.now(tz=timezone.utc),
            audience_group_id=audience_ref.audience_group_id,
            request_id=send_ref.request_id,
            recipient_count=len(req.user_ids),
            send_result_json=json.dumps({
                "status": send_ref.status,
                "reason": send_ref.reason,
                "projected_recipients": send_ref.projected_recipients,
            }),
        )
        db.add(record)
        db.commit()

        return CampaignResponse(
            campaign_id=campaign_id,
            simulation_id=req.simulation_id,
            variant_id=req.variant_id,
            status=record.status,
            audience_group_id=audience_ref.audience_group_id,
            request_id=send_ref.request_id,
            recipient_count=len(req.user_ids),
        )

    except Exception as exc:
        logger.exception("Campaign %s failed", campaign_id)
        record = CampaignRecord(
            campaign_id=campaign_id,
            simulation_id=req.simulation_id,
            segment_id=req.segment_id,
            variant_id=req.variant_id,
            status="failed",
            error=str(exc)[:500],
        )
        db.add(record)
        db.commit()
        raise HTTPException(status_code=502, detail=f"LINE API error: {exc}") from exc
    finally:
        connector.close()


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
) -> CampaignResponse:
    """Get campaign details."""
    record = db.get(CampaignRecord, campaign_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return CampaignResponse(
        campaign_id=record.campaign_id,
        simulation_id=record.simulation_id,
        variant_id=record.variant_id,
        status=record.status,
        audience_group_id=record.audience_group_id,
        request_id=record.request_id,
        recipient_count=record.recipient_count,
    )


@router.get("/{campaign_id}/stats", response_model=CampaignStatsResponse)
async def get_campaign_stats(
    campaign_id: str,
    db: Session = Depends(get_db),
) -> CampaignStatsResponse:
    """Poll LINE for current delivery stats and update the campaign record."""
    record = db.get(CampaignRecord, campaign_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not record.request_id:
        raise HTTPException(status_code=409, detail="Campaign has no send reference")

    connector = _get_line_connector()
    try:
        stats = connector.get_stats(record.request_id)

        # Persist latest stats
        record.stats_json = json.dumps({
            "requested": stats.requested,
            "delivered": stats.delivered,
            "failed": stats.failed,
            "opens": stats.opens,
            "clicks": stats.clicks,
            "status": stats.status,
        })
        if stats.status == "done":
            record.status = "completed"
        db.commit()

        return CampaignStatsResponse(
            campaign_id=campaign_id,
            status=record.status,
            requested=stats.requested,
            delivered=stats.delivered,
            failed=stats.failed,
            opens=stats.opens,
            clicks=stats.clicks,
            delivery_status=stats.status,
        )
    finally:
        connector.close()


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Pause a running campaign."""
    record = db.get(CampaignRecord, campaign_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if record.status not in ("sent", "proposed"):
        raise HTTPException(status_code=409, detail=f"Cannot pause campaign in '{record.status}' state")

    record.status = "paused"
    db.commit()

    if record.request_id:
        connector = _get_line_connector()
        try:
            connector.pause(record.request_id)
        finally:
            connector.close()

    return {"campaign_id": campaign_id, "status": "paused"}


@router.get("", response_model=list[CampaignSummary])
async def list_campaigns(
    db: Session = Depends(get_db),
    simulation_id: Optional[str] = None,
) -> list[CampaignSummary]:
    """List campaigns, optionally filtered by simulation_id."""
    query = db.query(CampaignRecord).order_by(CampaignRecord.created_at.desc())
    if simulation_id:
        query = query.filter_by(simulation_id=simulation_id)
    records = query.limit(50).all()

    return [
        CampaignSummary(
            campaign_id=r.campaign_id,
            simulation_id=r.simulation_id,
            variant_id=r.variant_id,
            segment_id=r.segment_id,
            status=r.status,
            created_at=r.created_at,
            recipient_count=r.recipient_count,
        )
        for r in records
    ]
