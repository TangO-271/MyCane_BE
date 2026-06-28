"""Multi-channel notification dispatch — LINE + Firebase Cloud Messaging.

Given a user's contact handles, push the alert to whatever channels are configured and
return the list that actually accepted it. Both channels degrade gracefully when their
credentials are missing (returns an empty list rather than raising), so persistence of the
in-app notification still succeeds and the inbox works without external services.
"""

import os
import requests
from loguru import logger
from app.services import line_client

FCM_LEGACY_URL = "https://fcm.googleapis.com/fcm/send"


def _fcm_send(fcm_token: str | None, title: str | None, body: str) -> bool:
    server_key = os.getenv("FCM_SERVER_KEY")
    if not server_key or not fcm_token:
        return False
    try:
        resp = requests.post(
            FCM_LEGACY_URL,
            headers={"Authorization": f"key={server_key}", "Content-Type": "application/json"},
            json={"to": fcm_token, "notification": {"title": title or "ตาสวรรค์", "body": body}},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"FCM send failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as exc:
        logger.warning(f"FCM send error: {exc}")
        return False


def dispatch_to_channels(
    line_user_id: str | None,
    fcm_token: str | None,
    title: str | None,
    message: str,
) -> list[str]:
    """Push to every configured channel; return the channels that accepted the message."""
    channels: list[str] = []
    line_text = f"{title}\n{message}" if title else message
    if line_user_id and line_client.push_message(line_user_id, line_text):
        channels.append("LINE")
    if fcm_token and _fcm_send(fcm_token, title, message):
        channels.append("Firebase Push")
    return channels
