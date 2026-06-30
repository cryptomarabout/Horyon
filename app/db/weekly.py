"""app.db.weekly — weekly macro digests."""
from __future__ import annotations

from datetime import date as date_t

from ._core import _conn, _execute, _fetchall, _fetchone


# --------------------------------------------------------------------------- #
# Weekly digest
# --------------------------------------------------------------------------- #
def insert_weekly_digest(week_start: "date_t", week_end: "date_t", content: str,
                          rotation: str = "MIXED", model_used: str = "",
                          trigger: str = "cron", duration_ms: int | None = None,
                          error: str | None = None,
                          market_snapshot: str | None = None) -> None:
    """Persist a weekly digest. ``market_snapshot`` is a JSON string (or None) of
    structured market levels (BTC/ETH price, dominance, total mcap, …) read by the
    Weekly web view's Market Snapshot block. The column is optional: if the
    additive migration hasn't been applied yet, we fall back to the legacy insert
    so the weekly cron is never broken pre-migration (mirrors insert_feed_items)."""
    try:
        _execute(
            """
            INSERT INTO weekly_digest
              (week_start, week_end, content, rotation, model_used, trigger,
               duration_ms, error, market_snapshot)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (week_start) DO UPDATE SET
              week_end        = EXCLUDED.week_end,
              content         = EXCLUDED.content,
              rotation        = EXCLUDED.rotation,
              model_used      = EXCLUDED.model_used,
              trigger         = EXCLUDED.trigger,
              duration_ms     = EXCLUDED.duration_ms,
              error           = EXCLUDED.error,
              market_snapshot = COALESCE(EXCLUDED.market_snapshot, weekly_digest.market_snapshot),
              created_at      = now()
            """,
            (week_start, week_end, content, rotation, model_used, trigger,
             duration_ms, error, market_snapshot),
        )
    except Exception as exc:
        if "market_snapshot" not in str(exc).lower():
            raise
        # Column not migrated yet — degrade to the legacy 8-column insert.
        _execute(
            """
            INSERT INTO weekly_digest
              (week_start, week_end, content, rotation, model_used, trigger, duration_ms, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (week_start) DO UPDATE SET
              week_end    = EXCLUDED.week_end,
              content     = EXCLUDED.content,
              rotation    = EXCLUDED.rotation,
              model_used  = EXCLUDED.model_used,
              trigger     = EXCLUDED.trigger,
              duration_ms = EXCLUDED.duration_ms,
              error       = EXCLUDED.error,
              created_at  = now()
            """,
            (week_start, week_end, content, rotation, model_used, trigger, duration_ms, error),
        )


def get_weekly_for_date(target_date: "date_t") -> "dict | None":
    """Return the weekly digest that covers target_date, or None."""
    return _fetchone(
        """
        SELECT week_start, week_end, content, rotation, model_used,
               to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
        FROM weekly_digest
        WHERE error IS NULL AND week_start <= %s AND week_end >= %s
        ORDER BY week_start DESC
        LIMIT 1
        """,
        (target_date, target_date),
        dict_rows=True,
    )


def list_weekly_digests(limit: int = 12) -> list[dict]:
    return _fetchall(
        """
        SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
               to_char(week_end,'YYYY-MM-DD')   AS week_end,
               rotation,
               to_char(created_at,'YYYY-MM-DD') AS created_at
        FROM weekly_digest WHERE error IS NULL
        ORDER BY week_start DESC LIMIT %s
        """,
        (limit,),
        dict_rows=True,
    )


def get_recent_weekly_digests(
    limit: int = 3, before_week_start: "date_t | None" = None
) -> list[dict]:
    """Return the N most recent successful weekly digests (optionally before a date).

    Used to inject previous-week context into the LLM prompt for continuity.
    """
    if before_week_start is not None:
        return _fetchall(
            """
            SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
                   to_char(week_end,'YYYY-MM-DD')   AS week_end,
                   content, rotation
            FROM weekly_digest
            WHERE error IS NULL
              AND (content IS NOT NULL AND content != '')
              AND week_start < %s
            ORDER BY week_start DESC LIMIT %s
            """,
            (before_week_start, limit),
            dict_rows=True,
        )
    return _fetchall(
        """
        SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
               to_char(week_end,'YYYY-MM-DD')   AS week_end,
               content, rotation
        FROM weekly_digest
        WHERE error IS NULL
          AND (content IS NOT NULL AND content != '')
        ORDER BY week_start DESC LIMIT %s
        """,
        (limit,),
        dict_rows=True,
    )


def get_weeks_needing_backfill() -> list[tuple]:
    """Return (week_start, week_end) for weeks with digests but no weekly_digest.

    Ordered oldest-first so backfill builds context incrementally.
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DATE_TRUNC('week', date)::date           AS week_start,
                   DATE_TRUNC('week', date)::date + 6       AS week_end
            FROM crypto_digest
            WHERE error IS NULL
            GROUP BY week_start, week_end
            ORDER BY week_start ASC
            """
        )
        all_weeks = cur.fetchall()
        cur.execute(
            "SELECT week_start FROM weekly_digest WHERE error IS NULL"
        )
        existing = {r[0] for r in cur.fetchall()}
        return [(r[0], r[1]) for r in all_weeks if r[0] not in existing]
