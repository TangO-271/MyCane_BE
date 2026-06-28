"""Notification dispatch — LINE only.

Pushes alerts to LINE via the Messaging API. Degrades gracefully when credentials
are missing so the in-app notification inbox still works without LINE configured.
"""

from app.services import line_client


def dispatch_to_channels(
    line_user_id: str | None,
    title: str | None,
    message: str,
) -> list[str]:
    """Push to every configured channel; return the channels that accepted the message."""
    channels: list[str] = []
    line_text = f"{title}\n{message}" if title else message
    if line_user_id and line_client.push_message(line_user_id, line_text):
        channels.append("LINE")
    return channels
