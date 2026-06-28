"""LINE Messaging API client.

Sends push/reply messages and verifies webhook signatures. Reads credentials from env
(LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET). If credentials are absent the client
degrades gracefully (returns False / skips) so the rest of the notification system keeps
working in environments without LINE configured yet.
"""

import os
import hmac
import hashlib
import base64
import requests
from loguru import logger

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"

# LINE caps a single text message at 5000 chars.
MAX_TEXT = 5000


def _access_token() -> str | None:
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN")


def is_configured() -> bool:
    return bool(_access_token())


def id_token_verification_enabled() -> bool:
    """True when the backend is configured to verify LINE id_tokens server-side."""
    return bool(os.getenv("LINE_LOGIN_CHANNEL_ID"))


def verify_id_token(id_token: str | None) -> str | None:
    """Verify a LINE ID token with LINE and return the verified userId (``sub``).

    Requires LINE_LOGIN_CHANNEL_ID (the LINE Login channel the LIFF app belongs to)
    as the expected audience. Returns None when verification is not configured, the
    token is missing/invalid/expired, or the audience does not match — callers must
    treat None as an authentication failure when verification is enabled.
    """
    channel_id = os.getenv("LINE_LOGIN_CHANNEL_ID")
    if not channel_id or not id_token:
        return None
    try:
        resp = requests.post(
            LINE_VERIFY_URL,
            data={"id_token": id_token, "client_id": channel_id},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"LINE id_token verify failed [{resp.status_code}]: {resp.text[:200]}")
            return None
        payload = resp.json()
        if payload.get("aud") != channel_id:
            logger.warning("LINE id_token audience mismatch")
            return None
        return payload.get("sub")
    except Exception as exc:  # network / timeout / bad JSON
        logger.warning(f"LINE id_token verify error: {exc}")
        return None


def verify_signature(body: bytes, signature: str | None) -> bool:
    """Verify the X-Line-Signature header (HMAC-SHA256 of the raw request body)."""
    secret = os.getenv("LINE_CHANNEL_SECRET", "").encode("utf-8")
    if not secret or not signature:
        return False
    digest = hmac.new(secret, body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def push_message(line_user_id: str | None, text: str) -> bool:
    """Push a text message to a LINE user. Returns True on a 200 from LINE."""
    token = _access_token()
    if not token or not line_user_id:
        return False
    try:
        resp = requests.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"to": line_user_id, "messages": [{"type": "text", "text": text[:MAX_TEXT]}]},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"LINE push failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as exc:  # network / timeout
        logger.warning(f"LINE push error: {exc}")
        return False


def reply_message(reply_token: str, text: str) -> bool:
    """Reply to a LINE event using its reply token (used by the webhook bridge)."""
    token = _access_token()
    if not token or not reply_token:
        return False
    try:
        resp = requests.post(
            LINE_REPLY_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"replyToken": reply_token, "messages": [{"type": "text", "text": text[:MAX_TEXT]}]},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"LINE reply failed [{resp.status_code}]: {resp.text[:200]}")
        return resp.status_code == 200
    except Exception as exc:
        logger.warning(f"LINE reply error: {exc}")
        return False
