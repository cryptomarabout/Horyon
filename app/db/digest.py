"""app.db.digest — daily digest rows, the single-row cache, and keyword-analysis logging."""
from __future__ import annotations

from datetime import date as date_t

from ._core import _conn, _execute, _fetchall, _fetchone


def get_cache() -> tuple[object | None, str]:
    row = _fetchone("SELECT last_run, last_analysis FROM crypto_cache LIMIT 1")
    if not row:
        return None, ""
    return row[0], (row[1] or "")


def set_cache(raw_analysis: str) -> None:
    """Keep crypto_cache as a single row (DELETE + INSERT)."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM crypto_cache")
        cur.execute(
            "INSERT INTO crypto_cache (last_run, last_analysis) VALUES (now(), %s)",
            (raw_analysis,),
        )


def insert_digest(d: date_t, content: str, model_used: str = "", trigger: str = "manual",
                  duration_ms: int | None = None, error: str | None = None) -> None:
    _execute(
        "INSERT INTO crypto_digest (created_at, date, content, model_used, trigger, duration_ms, error)"
        " VALUES (now(), %s, %s, %s, %s, %s, %s)",
        (d, content, model_used, trigger, duration_ms, error),
    )


def record_keyword_analysis(keyword: str, chat_id: str, model_used: str,
                             duration_ms: int | None = None) -> None:
    _execute(
        "INSERT INTO keyword_analysis (keyword, chat_id, model_used, duration_ms)"
        " VALUES (%s, %s, %s, %s)",
        (keyword[:500], chat_id, model_used, duration_ms),
    )


def get_digest(d: "date_t") -> "dict | None":
    """Return the most recent error-free digest for a date as a dict, or None."""
    row = _fetchone(
        """SELECT date, content, model_used, trigger
           FROM crypto_digest
           WHERE date = %s AND error IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (d,),
    )
    if row is None:
        return None
    return {"date": row[0], "content": row[1], "model_used": row[2], "trigger": row[3]}


def get_digest_attempts(d: "date_t") -> list[dict]:
    """All crypto_digest rows for a date (successes AND error rows), newest first.

    Drives the T17 same-day retry decision (app.digest.should_retry_digest): it needs
    to see failed attempts too, so unlike get_digest() it does not filter error IS NULL.
    `has_content` guards the degenerate error-NULL-but-empty case (a bare header body)."""
    rows = _fetchall(
        """SELECT created_at, trigger, error, (length(coalesce(content, '')) > 40) AS has_content
           FROM crypto_digest WHERE date = %s ORDER BY created_at DESC""",
        (d,),
    )
    return [
        {"created_at": r[0], "trigger": r[1], "error": r[2], "has_content": bool(r[3])}
        for r in rows
    ]


def get_recent_digests(limit: int = 15) -> list[tuple]:
    return _fetchall(
        """SELECT created_at, date, model_used, trigger, duration_ms, error
           FROM crypto_digest ORDER BY created_at DESC LIMIT %s""",
        (limit,),
    )


# ── Intraday digest updates (app/intraday.py) ───────────────────────────────
def insert_digest_update(d: date_t, seq: int, content: str, model_used: str = "",
                         window_start=None, item_count: int | None = None,
                         trigger: str = "cron", error: str | None = None) -> None:
    _execute(
        "INSERT INTO digest_updates"
        " (created_at, date, seq, content, model_used, window_start, item_count, trigger, error)"
        " VALUES (now(), %s, %s, %s, %s, %s, %s, %s, %s)",
        (d, seq, content, model_used, window_start, item_count, trigger, error),
    )


def get_digest_updates(d: date_t) -> list[dict]:
    """All error-free intraday update rows for a date, oldest first (the timeline order the
    web renders and app/intraday.py appends to). Empty list if none."""
    rows = _fetchall(
        """SELECT created_at, seq, content, model_used
           FROM digest_updates
           WHERE date = %s AND error IS NULL AND content IS NOT NULL AND content <> ''
           ORDER BY created_at ASC""",
        (d,),
    )
    return [
        {"created_at": r[0], "seq": r[1], "content": r[2], "model_used": r[3]}
        for r in rows
    ]


def get_last_covered_ts(d: date_t):
    """The most recent created_at among a date's morning digest (error-free) AND its intraday
    updates — the anchor app/intraday.py uses as `since_ts` for the next update's window.
    Returns a timestamptz or None (no successful digest/update yet today)."""
    row = _fetchone(
        """SELECT max(ts) FROM (
               SELECT created_at AS ts FROM crypto_digest
                 WHERE date = %s AND error IS NULL
                   AND content IS NOT NULL AND length(content) > 40
               UNION ALL
               SELECT created_at AS ts FROM digest_updates
                 WHERE date = %s AND error IS NULL
                   AND content IS NOT NULL AND content <> ''
           ) t""",
        (d, d),
    )
    return row[0] if row else None


def get_recent_keyword_analyses(limit: int = 15) -> list[tuple]:
    return _fetchall(
        """SELECT created_at, keyword, model_used, duration_ms
           FROM keyword_analysis ORDER BY created_at DESC LIMIT %s""",
        (limit,),
    )


def get_recent_digests_text(days: int = 3) -> list[tuple]:
    """Return (date, content) for the last N successful digest days."""
    return _fetchall(
        """
        SELECT DISTINCT ON (date) date, content
        FROM crypto_digest
        WHERE error IS NULL
          AND date >= CURRENT_DATE - %s::int
          AND date < CURRENT_DATE
        ORDER BY date DESC, created_at DESC
        """,
        (days,),
    )


def get_digest_contents_for_dedup(days: int, before_date: "date_t | None" = None) -> list[tuple]:
    """Return (date, content) rows for dedup lookback.

    before_date given: digests from [before_date - days, before_date).
    before_date None:  digests from the last N days (excluding today).
    """
    if before_date:
        return _fetchall(
            """
            SELECT DISTINCT ON (date) date, content
            FROM crypto_digest
            WHERE error IS NULL
              AND date < %s
              AND date >= %s - %s::int
            ORDER BY date DESC, created_at DESC
            """,
            (before_date, before_date, days),
        )
    return _fetchall(
        """
        SELECT DISTINCT ON (date) date, content
        FROM crypto_digest
        WHERE error IS NULL
          AND date >= CURRENT_DATE - %s::int
          AND date < CURRENT_DATE
        ORDER BY date DESC, created_at DESC
        """,
        (days,),
    )


def get_digests_for_range(start: "date_t", end: "date_t") -> list[tuple]:
    """Return (date, content) tuples for all digests within [start, end]."""
    return _fetchall(
        """
        SELECT DISTINCT ON (date) date, content
        FROM crypto_digest
        WHERE error IS NULL AND date >= %s AND date <= %s
        ORDER BY date DESC, created_at DESC
        """,
        (start, end),
    )
