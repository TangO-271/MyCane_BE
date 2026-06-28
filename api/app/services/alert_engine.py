"""Intelligent alert engine — ระบบแจ้งเตือนอัจฉริยะ.

Scans for per-plot threats and raises near-real-time alerts:
  1. Hotspot-near-plot — a VIIRS hotspot inside the plot's 30 m buffer.

Each alert is persisted to `notifications` (deduped per plot+hazard+day so we never spam)
and dispatched to the owner's LINE account via the dispatch service.

Run on a schedule (hooked into the hourly ingestion job in main.py) or on demand via
POST /api/v1/notifications/run-alert-scan.
"""

import sys
from pathlib import Path
from datetime import date

import psycopg2
from loguru import logger

sys.path.append(str(Path(__file__).resolve().parents[3]))
from pipeline.config import DATABASE_URL  # noqa: E402
from app.services.dispatch import dispatch_to_channels  # noqa: E402


def _insert_notification(cur, user_id, plot_id, title, message, hazard, severity, dedupe_key):
    """Insert a notification, skipping duplicates via the unique dedupe_key index.
    Returns the new id, or None if it already existed today."""
    cur.execute(
        """
        INSERT INTO notifications (user_id, plot_id, title, message, hazard_type, severity, dedupe_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id;
        """,
        (user_id, plot_id, title, message, hazard, severity, dedupe_key),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _set_channels(cur, notification_id, channels):
    cur.execute(
        "UPDATE notifications SET channels = %s WHERE id = %s;",
        (",".join(channels), notification_id),
    )


def run_alert_scan(hotspot_buffer_m: int = 30, hotspot_hours: int = 24) -> dict:
    """Scan all plots for active hotspot threats and raise alerts. Idempotent within a day."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    created = 0
    today = date.today().isoformat()
    try:
        cur.execute(
            """
            SELECT p.id, p.user_id, p.plot_name, u.line_user_id,
                   MIN(ST_Distance(h.geometry, p.geometry)) AS dist_m,
                   COUNT(h.id) AS n
            FROM plots p
            JOIN users u ON u.id = p.user_id
            JOIN hotspots h ON ST_DWithin(h.geometry, p.geometry, %s)
            WHERE h.acq_time >= NOW() - make_interval(hours => %s)
            GROUP BY p.id, p.user_id, p.plot_name, u.line_user_id;
            """,
            (hotspot_buffer_m, hotspot_hours),
        )
        for plot_id, user_id, plot_name, line_user_id, dist_m, n in cur.fetchall():
            if user_id is None:
                continue
            dedupe = f"hotspot:{plot_id}:{today}"
            title = "เตือนภัยไฟ — พบจุดความร้อนใกล้แปลงอ้อย"
            message = (
                f"พบจุดความร้อน {int(n)} จุด ใกล้แปลง {plot_name} "
                f"(ใกล้ที่สุด ~{float(dist_m):.0f} ม.) ใบอ้อยแห้งติดไฟง่ายมาก — "
                f"โปรดเตรียมแนวกันไฟและแจ้งเจ้าหน้าที่หากไฟลุกลาม"
            )
            nid = _insert_notification(cur, user_id, plot_id, title, message, "fire", "danger", dedupe)
            if nid:
                created += 1
                channels = dispatch_to_channels(line_user_id, title, message)
                _set_channels(cur, nid, channels)

        conn.commit()
        logger.info(f"🔔 Alert scan complete — {created} new alert(s) raised.")
        return {"status": "success", "alerts_created": created}
    except Exception as exc:
        conn.rollback()
        logger.error(f"Alert scan failed: {exc}")
        return {"status": "error", "detail": str(exc), "alerts_created": created}
    finally:
        cur.close()
        conn.close()
