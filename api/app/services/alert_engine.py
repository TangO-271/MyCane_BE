"""Intelligent alert engine — the proposal's "ระบบแจ้งเตือนอัจฉริยะ".

Scans for per-plot threats and raises near-real-time alerts:
  1. Hotspot-near-plot  — a VIIRS hotspot inside the plot's 30 m buffer (proposal §3 buffer
     zone). Works on the deployed pipeline's `hotspots` table WITHOUT the AI service.
  2. Risk-threshold      — a plot's latest AI risk score (plot_risk_scores) crosses อันตราย.

Each alert is persisted to `notifications` (deduped per plot+hazard+day so we never spam)
and dispatched to the owner's LINE + FCM via the dispatch service. Per the spec this is a
BACKEND-owned, server-side trigger — the FE never raises alerts (line-architecture.md §3).

Run on a schedule (hooked into the hourly ingestion job in main.py) or on demand via
POST /api/v1/notifications/run-alert-scan.
"""

import sys
from pathlib import Path
from datetime import date

import psycopg2
from loguru import logger

# Reuse the pipeline's DATABASE_URL (same source main.py uses for raw SQL).
sys.path.append(str(Path(__file__).resolve().parents[3]))
from pipeline.config import DATABASE_URL  # noqa: E402
from app.services.dispatch import dispatch_to_channels  # noqa: E402

# Risk-level thresholds — match the FE riskState mapping:
#   >= 0.80 => อันตราย (danger), 0.60–0.79 => เฝ้าระวัง (warn), < 0.60 => ปกติดี (no alert).
DANGER_THRESHOLD = 0.8
WARN_THRESHOLD = 0.6

HAZARD_TH = {
    "fire": "ไฟ",
    "flood": "น้ำท่วม",
    "drought": "แล้ง",
    "disease": "โรคพืช",
}


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
    """Scan all plots for active threats and raise alerts. Idempotent within a day."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    created = 0
    today = date.today().isoformat()
    try:
        # ── 1) Hotspot near plot (30 m buffer, recent acquisitions) ──
        cur.execute(
            """
            SELECT p.id, p.user_id, p.plot_name, u.line_user_id, u.fcm_token,
                   MIN(ST_Distance(h.geometry, p.geometry)) AS dist_m,
                   COUNT(h.id) AS n
            FROM plots p
            JOIN users u ON u.id = p.user_id
            JOIN hotspots h ON ST_DWithin(h.geometry, p.geometry, %s)
            WHERE h.acq_time >= NOW() - make_interval(hours => %s)
            GROUP BY p.id, p.user_id, p.plot_name, u.line_user_id, u.fcm_token;
            """,
            (hotspot_buffer_m, hotspot_hours),
        )
        for plot_id, user_id, plot_name, line_user_id, fcm_token, dist_m, n in cur.fetchall():
            if user_id is None:
                continue
            dedupe = f"hotspot:{plot_id}:{today}"
            title = "เตือนภัยไฟ — พบจุดความร้อนใกล้แปลง"
            message = (
                f"พบจุดความร้อน {int(n)} จุด ใกล้แปลง {plot_name} "
                f"(ใกล้ที่สุด ~{float(dist_m):.0f} ม.) โปรดเฝ้าระวังไฟลามและเตรียมแนวกันไฟรอบแปลง"
            )
            nid = _insert_notification(cur, user_id, plot_id, title, message, "fire", "danger", dedupe)
            if nid:
                created += 1
                channels = dispatch_to_channels(line_user_id, fcm_token, title, message)
                _set_channels(cur, nid, channels)

        # ── 2) Risk-threshold crossing (latest AI score per plot) ──
        cur.execute(
            """
            SELECT DISTINCT ON (r.plot_id)
                   r.plot_id, p.user_id, p.plot_name, u.line_user_id, u.fcm_token,
                   r.fire_risk_score, r.flood_risk_score, r.drought_risk_score, r.disease_risk_score
            FROM plot_risk_scores r
            JOIN plots p ON p.id = r.plot_id
            JOIN users u ON u.id = p.user_id
            ORDER BY r.plot_id, r.evaluated_at DESC;
            """
        )
        for plot_id, user_id, plot_name, line_user_id, fcm_token, fire, flood, drought, disease in cur.fetchall():
            if user_id is None:
                continue
            scores = {"fire": fire, "flood": flood, "drought": drought, "disease": disease}
            for hazard, score in scores.items():
                if score is None:
                    continue
                s = float(score)
                if s >= DANGER_THRESHOLD:
                    severity, level_th = "danger", "อันตราย"
                    advice = "โปรดดูคำแนะนำเชิงปฏิบัติในแอปตาสวรรค์"
                elif s >= WARN_THRESHOLD:
                    severity, level_th = "warn", "เฝ้าระวัง"
                    advice = "โปรดเฝ้าติดตามสถานการณ์ และดูคำแนะนำในแอปตาสวรรค์"
                else:
                    continue  # ปกติดี — no alert
                label = HAZARD_TH[hazard]
                # Level is part of the key so an escalation (warn → danger on the same day)
                # still raises a fresh alert instead of being deduped away.
                dedupe = f"risk:{hazard}:{severity}:{plot_id}:{today}"
                title = f"เตือนภัย{label} — ระดับ{level_th}"
                message = (
                    f"แปลง {plot_name} มีความเสี่ยง{label}ในระดับ{level_th} "
                    f"(คะแนน {s:.2f}) {advice}"
                )
                nid = _insert_notification(cur, user_id, plot_id, title, message, hazard, severity, dedupe)
                if nid:
                    created += 1
                    channels = dispatch_to_channels(line_user_id, fcm_token, title, message)
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
