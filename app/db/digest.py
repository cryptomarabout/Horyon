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


def get_recent_digests(limit: int = 15) -> list[tuple]:
    return _fetchall(
        """SELECT created_at, date, model_used, trigger, duration_ms, error
           FROM crypto_digest ORDER BY created_at DESC LIMIT %s""",
        (limit,),
    )


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
