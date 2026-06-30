"""app.db.threads — rendered Twitter/X threads (one per digest date)."""
from __future__ import annotations

import json
from datetime import date as date_t

from ._core import _execute, _fetchall, _fetchone


# --------------------------------------------------------------------------- #
# Twitter/X threads (see app/threads.py) — one rendered thread per digest date
# --------------------------------------------------------------------------- #
def upsert_thread(digest_date: "date_t", hook: str, tweets: list[dict], cta: str = "",
                  og_image_url: str = "", model_used: str = "", status: str = "pending") -> None:
    """Upsert the rendered thread for a date. ``status`` defaults to 'pending' (fresh render);
    the pre-publish gate passes 'blocked' when a tweet trips the modality check so the external
    poster (which only picks up 'pending') never ships it."""
    _execute(
        """
        INSERT INTO digest_threads
            (digest_date, hook, tweets, cta, og_image_url, model_used, status, created_at)
        VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, now())
        ON CONFLICT (digest_date) DO UPDATE SET
            hook         = EXCLUDED.hook,
            tweets       = EXCLUDED.tweets,
            cta          = EXCLUDED.cta,
            og_image_url = EXCLUDED.og_image_url,
            model_used   = EXCLUDED.model_used,
            status       = EXCLUDED.status,
            created_at   = now()
        """,
        (digest_date, hook, json.dumps(tweets), cta, og_image_url, model_used, status),
    )


def get_thread(digest_date: "date_t") -> "dict | None":
    """Return the stored thread for a date (tweets parsed from JSONB) or None."""
    row = _fetchone(
        """SELECT digest_date, hook, tweets, cta, og_image_url, model_used, status, created_at
           FROM digest_threads WHERE digest_date = %s""",
        (digest_date,),
        dict_rows=True,
    )
    return dict(row) if row else None


def get_digest_dates_without_thread() -> list["date_t"]:
    """Digest dates that have bullet analyses but no rendered thread (for backfill)."""
    return [r[0] for r in _fetchall(
        """
        SELECT DISTINCT a.digest_date
        FROM digest_bullet_analysis a
        LEFT JOIN digest_threads t ON t.digest_date = a.digest_date
        WHERE t.digest_date IS NULL
        ORDER BY a.digest_date
        """
    )]
