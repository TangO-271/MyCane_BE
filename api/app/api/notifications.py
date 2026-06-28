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
    summary="Broadcast notification alert via LINE",
    description=NOTIFICATION_SEND_DESC,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Alert persisted and dispatched to each target user's LINE account.",
            "content": {
                "application/json": {
                    "example": {
                        "status": "success",
                        "message": "Notification queued for 3 users.",
                        "channels": ["LINE"],
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
    """Persist a notification per target user and dispatch via LINE."""
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
        channels = dispatch_to_channels(target.line_user_id, req.title, req.message)
        note.channels = ",".join(channels) if channels else None
        all_channels.update(channels)
        delivered += 1
    db.commit()

    return {
        "status": "success",
        "message": f"Notification queued for {delivered} users.",
        "channels": sorted(all_channels) if all_channels else [],
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
    summary="Run the hotspot alert engine (server-side trigger)",
    description=(
        "Scans every plot for VIIRS hotspots within the 30 m buffer and raises deduped alerts "
        "to plot owners via LINE. Normally invoked by the hourly scheduler; exposed here for "
        "manual/cron triggering. Idempotent within a day per plot+hazard."
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
