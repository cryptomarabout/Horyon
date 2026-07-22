"""app.db.alerts — bookkeeping for breaking-news Telegram alerts (app/alerts.py).

One row per alerted feed-item link. Drives dedup (never re-alert the same story as more
outlets pick it up) and the per-hour/per-day rate cap. Deterministic path — no LLM here.
Bot-only: the web never reads alerts_sent (alerts are Telegram-only).
"""
from __future__ import annotations

from psycopg2.extras import execute_values

from ._core import _conn, _fetchall, _fetchone


def insert_alerts_sent(rows: list[dict]) -> int:
    """Record alerted items. `rows` = [{link, title, score, category, source_key}].
    ON CONFLICT (link) DO NOTHING — an already-alerted link is a harmless no-op.
    Returns the number of new rows inserted."""
    if not rows:
        return 0
    values = [
        (r["link"], (r.get("title") or "")[:512], r.get("score"),
         (r.get("category") or "")[:32], (r.get("source_key") or "")[:256])
        for r in rows if r.get("link")
    ]
    if not values:
        return 0
    with _conn() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO alerts_sent (link, title, score, category, source_key)"
            " VALUES %s ON CONFLICT (link) DO NOTHING",
            values,
        )
        return cur.rowcount


def get_recent_alerts(hours: int) -> list[dict]:
    """Alerts sent within the last `hours` hours (title + link + sent_at), newest first —
    the dedup corpus (semantic match against new candidates) and the rate-cap source."""
    rows = _fetchall(
        """SELECT link, title, score, category, sent_at
           FROM alerts_sent
           WHERE sent_at >= now() - make_interval(hours => %s)
           ORDER BY sent_at DESC""",
        (hours,),
        dict_rows=True,
    )
    return rows


def count_alerts_since(minutes: int) -> int:
    """How many alerts were sent in the last `minutes` minutes (rate-cap check)."""
    row = _fetchone(
        "SELECT count(*) FROM alerts_sent WHERE sent_at >= now() - make_interval(mins => %s)",
        (minutes,),
    )
    return int(row[0]) if row else 0
