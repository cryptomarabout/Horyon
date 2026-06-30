"""app.db.analysis — analyst notes + per-bullet pre-computed analysis & importance scores."""
from __future__ import annotations

import json
from datetime import date as date_t

from ._core import _conn, _execute, _fetchall, _term_boundary_regex


# --------------------------------------------------------------------------- #
# Analyst notes
# --------------------------------------------------------------------------- #
def insert_analyst_notes(d: "date_t", notes: str, entity_updates: dict,
                          source: str = "digest", model_used: str = "") -> None:
    _execute(
        "INSERT INTO analyst_notes (date, notes, entity_updates, source, model_used)"
        " VALUES (%s, %s, %s::jsonb, %s, %s)",
        (d, notes, json.dumps(entity_updates), source, model_used),
    )


def upsert_bullet_analyses(digest_date: "date_t", items: list[dict]) -> int:
    """Upsert pre-computed analyst text + importance score for each bullet.

    items: [{title, body, analysis, model_used, importance_score?, source_count?, score_breakdown?}].
    Scoring fields are optional — None when scoring failed (digest must never break on it).
    Returns count upserted.
    """
    if not items:
        return 0
    with _conn() as conn, conn.cursor() as cur:
        count = 0
        for it in items:
            breakdown = it.get("score_breakdown")
            cur.execute(
                """
                INSERT INTO digest_bullet_analysis
                    (digest_date, title, body, analysis, model_used,
                     importance_score, source_count, score_breakdown)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (digest_date, title) DO UPDATE SET
                    body             = EXCLUDED.body,
                    analysis         = EXCLUDED.analysis,
                    model_used       = EXCLUDED.model_used,
                    importance_score = EXCLUDED.importance_score,
                    source_count     = EXCLUDED.source_count,
                    score_breakdown  = EXCLUDED.score_breakdown,
                    created_at       = now()
                """,
                (digest_date, it["title"], it.get("body", ""), it["analysis"],
                 it.get("model_used", ""), it.get("importance_score"),
                 it.get("source_count"),
                 json.dumps(breakdown) if breakdown is not None else None),
            )
            count += cur.rowcount
    return count


def get_bullet_analyses(digest_date: "date_t") -> dict:
    """Return {title: analysis} for all bullets of the given digest date."""
    rows = _fetchall(
        "SELECT title, analysis FROM digest_bullet_analysis WHERE digest_date = %s",
        (digest_date,),
    )
    return {row[0]: row[1] for row in rows}


def get_bullet_analyses_full(digest_date: "date_t") -> list[dict]:
    """All analysis rows for one digest date (title, body, analysis, scores), importance desc."""
    return [dict(r) for r in _fetchall(
        """
        SELECT title, body, analysis, importance_score, source_count
        FROM digest_bullet_analysis
        WHERE digest_date = %s
        ORDER BY importance_score DESC NULLS LAST
        """,
        (digest_date,),
        dict_rows=True,
    )]


def get_prior_bullets_matching_terms(terms: list[str], before_date: "date_t", days: int = 7,
                                     limit: int = 6) -> list[dict]:
    """Previously-published digest bullets (already-vetted, grounded) whose title or body
    word-boundary-matches any of `terms`, from the `days` before `before_date` (exclusive).
    Used to give the audio briefing CONTINUITY — 'this builds on earlier coverage'. Most recent
    first; returns (digest_date, title, analysis)."""
    pattern = _term_boundary_regex(terms)
    if not pattern:
        return []
    return [dict(r) for r in _fetchall(
        """
        SELECT digest_date, title, analysis
        FROM digest_bullet_analysis
        WHERE (title ~* %s OR body ~* %s)
          AND digest_date <  %s::date
          AND digest_date >= %s::date - make_interval(days => %s)
        ORDER BY digest_date DESC, importance_score DESC NULLS LAST
        LIMIT %s
        """,
        (pattern, pattern, before_date, before_date, days, limit),
        dict_rows=True,
    )]


def delete_bullet_analyses(digest_date: "date_t") -> int:
    """Delete all pre-computed analyses for a given digest date. Returns count deleted."""
    return _execute(
        "DELETE FROM digest_bullet_analysis WHERE digest_date = %s",
        (digest_date,),
    )


def prune_bullet_analyses(digest_date: "date_t", keep_titles: list[str]) -> int:
    """Delete rows for the date whose title is NOT in keep_titles — stale rows left by
    a superseded same-day digest run. No-op when keep_titles is empty (safety guard).
    Returns count deleted."""
    keep = [t for t in keep_titles if t]
    if not keep:
        return 0
    return _execute(
        "DELETE FROM digest_bullet_analysis WHERE digest_date = %s "
        "AND title <> ALL(%s::text[])",
        (digest_date, keep),
    )


def get_all_digest_bullets(limit: int = 20000) -> list[dict]:
    """All curated digest bullets (title + body), newest first. Feeds the entity
    "Horyon coverage" count in app/entity_graph.py — how many distinct daily-brief
    bullets cite each entity (distinct from raw source mention_count). The table is
    small (one digest/day × ~10–20 bullets) so a full scan is cheap."""
    return [dict(r) for r in _fetchall(
        "SELECT title, body FROM digest_bullet_analysis "
        "ORDER BY digest_date DESC LIMIT %s",
        (limit,),
        dict_rows=True,
    )]


def get_recent_analyst_notes(days: int = 7) -> list[tuple]:
    """Return (date, notes) rows for the last N days, newest first."""
    return _fetchall(
        "SELECT date, notes FROM analyst_notes"
        " WHERE date >= CURRENT_DATE - %s::int"
        " ORDER BY date DESC, id DESC",
        (days,),
    )


# --------------------------------------------------------------------------- #
# Narratives (see app/narratives.py) — gather sources + full-rebuild persist
# --------------------------------------------------------------------------- #
def get_bullet_analyses_window(days: int = 14) -> list[dict]:
    """News signals: per-bullet rows from the last N digest days (newest first)."""
    return [dict(r) for r in _fetchall(
        """
        SELECT id, digest_date, title, body, analysis, importance_score, source_count
        FROM digest_bullet_analysis
        WHERE digest_date >= CURRENT_DATE - %s::int
        ORDER BY digest_date DESC, importance_score DESC NULLS LAST
        """,
        (days,),
        dict_rows=True,
    )]
