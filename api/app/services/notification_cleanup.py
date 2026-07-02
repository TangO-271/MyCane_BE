"""Stale-notification purge — ล้างการแจ้งเตือนที่ค้างเก่า.

Notifications are never expired by the app: fire alerts linger after the hotspot is
gone, disease/flood/drought alerts (once raised) stay even after the weather condition
that triggered them resolves, deleting a plot leaves orphaned rows, and read alerts pile
up. This service deletes stale rows on a set of rules, run alongside the alert scan
(scheduler, every 30 min) and exposed via POST /api/v1/notifications/purge-stale.

Rules (each a single DELETE, run in order in one transaction; a row removed by an
earlier rule is not recounted by a later one):
  1. age-cap    — created_at older than NOTIF_MAX_AGE_DAYS
  2. read-aged  — is_read AND created_at older than NOTIF_READ_AGE_DAYS
  3. orphaned   — plot-scoped hazard with plot_id NULL (plot deleted), older than NOTIF_ORPHAN_AGE_DAYS
  4. resolved   — plot-scoped hazard whose plot's LATEST features no longer meet the
                  (warn-level) condition that would raise it — the "stale disease" case
  5. mock       — dedupe_key LIKE 'mock:%' (seeded test data)

Rule 4 uses warn-level cutoffs (not danger) so a row is only removed once the condition
has clearly resolved, never while it's merely de-escalating danger→warn.

dry_run runs every DELETE inside a transaction and rolls back, so the reported counts
are exactly what a real run would delete — no separate SELECT path to drift out of sync.
"""

import os

import psycopg2
from loguru import logger

import app.core.constants as const

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Ordered so broad age rules run before the narrower condition rule.
_RULES = (
    ("age_cap", """
        DELETE FROM notifications
        WHERE created_at < NOW() - make_interval(days => %(max_age)s)
    """),
    ("read_aged", """
        DELETE FROM notifications
        WHERE is_read = TRUE
          AND created_at < NOW() - make_interval(days => %(read_age)s)
    """),
    ("orphaned", """
        DELETE FROM notifications
        WHERE plot_id IS NULL
          AND hazard_type = ANY(%(scoped)s)
          AND created_at < NOW() - make_interval(days => %(orphan_age)s)
    """),
    ("resolved", """
        DELETE FROM notifications n
        USING (
            SELECT DISTINCT ON (plot_id) plot_id,
                   humidity_pct, rain_7d_mm, nearest_hotspot_km, hotspot_count_24h, spi_30d
            FROM plot_features
            ORDER BY plot_id, timestamp DESC
        ) lf
        WHERE n.plot_id = lf.plot_id
          AND n.hazard_type = ANY(%(scoped)s)
          AND (
               (n.hazard_type = 'fire'
                    AND COALESCE(lf.nearest_hotspot_km, %(missing_km)s) >= %(fire_warn_km)s
                    AND COALESCE(lf.hotspot_count_24h, 0) <= %(fire_warn_cnt)s)
            OR (n.hazard_type = 'disease'
                    AND COALESCE(lf.humidity_pct, 0) <= %(dis_warn_hum)s
                    AND COALESCE(lf.rain_7d_mm, 0) <= %(dis_warn_rain)s)
            OR (n.hazard_type = 'flood'
                    AND COALESCE(lf.rain_7d_mm, 0) <= %(flood_warn_rain)s)
            OR (n.hazard_type = 'drought'
                    AND COALESCE(lf.spi_30d, 0) >= %(drought_spi)s)
          )
    """),
    ("mock", """
        DELETE FROM notifications
        WHERE dedupe_key LIKE 'mock:%%'
    """),
)


def purge_stale_notifications(dry_run: bool = False) -> dict:
    """Delete stale notifications. When dry_run, roll back so nothing is persisted but the
    per-rule counts still reflect exactly what a real run would remove.
    Returns {"status", "dry_run", "deleted", "by_rule": {...}}."""
    params = {
        "max_age": const.NOTIF_MAX_AGE_DAYS,
        "read_age": const.NOTIF_READ_AGE_DAYS,
        "orphan_age": const.NOTIF_ORPHAN_AGE_DAYS,
        "scoped": list(const.PLOT_SCOPED_HAZARDS),
        "missing_km": const.DEFAULT_MISSING_HOTSPOT_KM,
        "fire_warn_km": const.FIRE_WARN_KM,
        "fire_warn_cnt": const.FIRE_WARN_COUNT_24H,
        "dis_warn_hum": const.DISEASE_WARN_HUMIDITY,
        "dis_warn_rain": const.DISEASE_WARN_RAIN_7D,
        "flood_warn_rain": const.FLOOD_WARN_RAIN_7D,
        "drought_spi": const.DROUGHT_SPI_30D,
    }

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    by_rule: dict[str, int] = {}
    total = 0
    try:
        for name, sql in _RULES:
            cur.execute(sql, params)
            by_rule[name] = cur.rowcount
            total += cur.rowcount
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        logger.info(f"🧹 Notification purge ({'dry-run' if dry_run else 'applied'}): {total} stale — {by_rule}")
        return {"status": "success", "dry_run": dry_run, "deleted": total, "by_rule": by_rule}
    except Exception as exc:
        conn.rollback()
        logger.error(f"Notification purge failed: {exc}")
        return {"status": "error", "detail": str(exc), "deleted": total, "by_rule": by_rule}
    finally:
        cur.close()
        conn.close()
