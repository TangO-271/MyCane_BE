from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List
from sqlalchemy.orm import Session

from app.core.config import get_db
from app.api.auth import get_current_user
from app.models.domain import User, Notification
from app.schemas.domain import NotificationResponse
from app.services.dispatch import dispatch_to_channels
from app.services.alert_engine import run_alert_scan
from app.services.ai_risk_sync import sync_ai_risk_scores

from app.docs.descriptions import NOTIFICATION_SEND_DESC

router = APIRouter()


class NotificationRequest(BaseModel):
    message: str = Field(
        ...,
        description="The push alert message content to deliver to active farmer devices",
        example="⚠️ พรุ่งนี้คาดมีฝนตกหนักและลมแรง โปรดยึดโรงเรือนและคลุมผลผลิต",
    )
    target_users: List[int] = Field(
        ...,
        description="List of database User IDs targeted to receive the notification broadcast",
        example=[1, 2, 15],
    )
    title: str = Field(default="แจ้งเตือนจากตาสวรรค์", description="Short alert headline", example="แจ้งเตือนสภาพอากาศ")


@router.get(
    "",
    response_model=List[NotificationResponse],
    status_code=status.HTTP_200_OK,
    summary="List the current user's notifications (inbox)",
    description="Returns the authenticated user's notification inbox, newest first. Drives the in-app /notifications screen.",
    responses={
        status.HTTP_200_OK: {"description": "Notification inbox retrieved."},
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials.",
            "content": {"application/json": {"example": {"detail": "Could not validate credentials"}}},
        },
    },
)
def list_notifications(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the authenticated user's notifications, most recent first."""
    rows = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return rows


@router.post(
    "/send",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Broadcast multi-channel notification alert",
    description=NOTIFICATION_SEND_DESC,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Alert persisted and dispatched to each target user's configured channels.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "success",
                        "message": "Notification queued for 3 users.",
                        "channels": ["LINE", "Firebase Push"],
                    }
                }
            },
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Invalid, expired, or missing JWT credentials.",
            "content": {"application/json": {"example": {"detail": "Could not validate credentials"}}},
        },
    },
)
def send_notification(
    req: NotificationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persist a notification per target user and dispatch to LINE + Firebase."""
    all_channels: set[str] = set()
    delivered = 0
    for uid in req.target_users:
        target = db.query(User).filter(User.id == uid).first()
        if not target:
            continue
        note = Notification(
            user_id=target.id,
            title=req.title,
            message=req.message,
            hazard_type="system",
            severity="info",
        )
        db.add(note)
        channels = dispatch_to_channels(target.line_user_id, target.fcm_token, req.title, req.message)
        note.channels = ",".join(channels) if channels else None
        all_channels.update(channels)
        delivered += 1
    db.commit()

    return {
        "status": "success",
        "message": f"Notification queued for {delivered} users.",
        "channels": sorted(all_channels) if all_channels else ["LINE", "Firebase Push"],
    }


@router.post(
    "/{notification_id}/read",
    status_code=status.HTTP_200_OK,
    summary="Mark a notification as read",
    responses={
        status.HTTP_200_OK: {"description": "Notification marked read.", "content": {"application/json": {"example": {"message": "ok"}}}},
        status.HTTP_404_NOT_FOUND: {"description": "Notification not found or not owned by the user."},
    },
)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == current_user.id)
        .first()
    )
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    note.is_read = True
    db.commit()
    return {"message": "ok"}


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a notification",
    description="Permanently delete one of the authenticated user's notifications.",
    responses={
        status.HTTP_200_OK: {"description": "Notification deleted.", "content": {"application/json": {"example": {"message": "Notification deleted successfully"}}}},
        status.HTTP_404_NOT_FOUND: {"description": "Notification not found or not owned by the user."},
    },
)
def delete_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    note = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == current_user.id)
        .first()
    )
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    db.delete(note)
    db.commit()
    return {"message": "Notification deleted successfully"}


@router.post(
    "/run-alert-scan",
    status_code=status.HTTP_200_OK,
    summary="Run the intelligent alert engine (server-side trigger)",
    description=(
        "Scans every plot for near-real-time threats (hotspot inside the 30 m buffer, and "
        "AI risk scores crossing the danger threshold) and raises deduped alerts to plot "
        "owners via LINE + Firebase. Normally invoked by the hourly scheduler; exposed here "
        "for manual/cron triggering. Idempotent within a day per plot+hazard."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "Scan completed.",
            "content": {"application/json": {"example": {"status": "success", "alerts_created": 2}}},
        },
    },
)
def trigger_alert_scan(current_user: User = Depends(get_current_user)):
    """Manually trigger the alert engine (the same job the scheduler runs hourly)."""
    return run_alert_scan()


@router.post(
    "/run-ai-sync",
    status_code=status.HTTP_200_OK,
    summary="Sync AI risk scores from DOH into plot_risk_scores (server-to-server)",
    description=(
        "Calls DOH /v1/risk-score for every registered plot using the stored GeoJSON geometry "
        "and writes the four per-hazard scores into plot_risk_scores. The alert engine then "
        "picks up the fresh scores on its next run. Normally invoked by the hourly scheduler "
        "(after pipeline ingestion); exposed here for on-demand triggering."
    ),
    responses={
        status.HTTP_200_OK: {
            "description": "Sync completed.",
            "content": {
                "application/json": {
                    "example": {"status": "success", "synced": 5, "failed": 0}
                }
            },
        },
    },
)
def trigger_ai_sync(current_user: User = Depends(get_current_user)):
    """Manually trigger the DOH AI risk score sync (the same step the scheduler runs)."""
    return sync_ai_risk_scores()
