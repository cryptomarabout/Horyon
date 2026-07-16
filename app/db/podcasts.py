"""app.db.podcasts — podcast episodes (transcripts + LLM analysis) + prediction follow-through."""
from __future__ import annotations

import json
from datetime import datetime

from ._core import _conn, _execute, _fetchall, _vec_literal


# --------------------------------------------------------------------------- #
# Podcast episodes (YouTube transcripts + LLM analysis)
# --------------------------------------------------------------------------- #
def insert_podcast_episode(ep: dict) -> bool:
    """Insert a freshly-detected episode (status='pending'). Dedupe on video_id.

    Returns True if a new row was inserted, False if it already existed.
    """
    return _execute(
        """
        INSERT INTO podcast_episodes (video_id, channel, channel_id, title, url, published_at)
        VALUES (%s, %s, %s, %s, %s, %s::timestamptz)
        ON CONFLICT (video_id) DO NOTHING
        """,
        (ep["video_id"], ep["channel"], ep.get("channel_id"),
         ep.get("title"), ep.get("url"), ep.get("published_at")),
    ) > 0


def get_pending_podcast_episodes(limit: int = 8) -> list[dict]:
    """Episodes awaiting transcript+analysis, newest first.

    'pending' (never attempted) is always eligible. 'failed' episodes back off:
    only retried once their last attempt (fetched_at) is older than 24 h, so a
    permanently IP-blocked video isn't re-hit every cron run.
    """
    return [dict(r) for r in _fetchall(
        """
        SELECT video_id, channel, channel_id, title, url, published_at, status
        FROM podcast_episodes
        WHERE status = 'pending'
           OR (status = 'failed'
               AND (fetched_at IS NULL OR fetched_at < now() - interval '24 hours'))
        ORDER BY published_at DESC NULLS LAST
        LIMIT %s
        """,
        (limit,),
        dict_rows=True,
    )]


def store_podcast_summary(video_id: str, transcript: str, transcript_lang: str | None,
                          duration_sec: int | None, summary: str, analysis: dict,
                          model_used: str, embedding: list[float] | None) -> None:
    """Persist transcript + map-reduce analysis; flip status to 'summarized'."""
    vec = _vec_literal(embedding) if embedding else None
    _execute(
        """
        UPDATE podcast_episodes SET
            transcript      = %s,
            transcript_lang = %s,
            duration_sec    = %s,
            summary         = %s,
            analysis        = %s::jsonb,
            model_used      = %s,
            embedding       = %s::vector,
            status          = 'summarized',
            error           = NULL,
            fetched_at      = COALESCE(fetched_at, now()),
            summarized_at   = now()
        WHERE video_id = %s
        """,
        (transcript, transcript_lang, duration_sec, summary,
         json.dumps(analysis), model_used, vec, video_id),
    )


def mark_podcast_episode(video_id: str, status: str, error: str | None = None) -> None:
    """Set a terminal status ('failed' or 'skipped') with an optional error note."""
    _execute(
        "UPDATE podcast_episodes SET status = %s, error = %s, fetched_at = now() WHERE video_id = %s",
        (status, error, video_id),
    )


def get_recent_podcast_summaries(hours: int = 48) -> list[dict]:
    """Summarized episodes published within the window, newest first — for digest context."""
    return [dict(r) for r in _fetchall(
        """
        SELECT video_id, channel, title, url, published_at, summary, analysis
        FROM podcast_episodes
        WHERE status = 'summarized'
          AND COALESCE(published_at, created_at) >= now() - make_interval(hours => %s)
        ORDER BY published_at DESC NULLS LAST
        """,
        (hours,),
        dict_rows=True,
    )]


def get_podcast_summaries_window(days: int = 14) -> list[dict]:
    """Podcast signals: summarized episodes within the window, newest first."""
    return [dict(r) for r in _fetchall(
        """
        SELECT video_id, channel, title, url, published_at, summary, analysis
        FROM podcast_episodes
        WHERE status = 'summarized' AND analysis IS NOT NULL
          AND COALESCE(published_at, created_at) >= now() - make_interval(days => %s)
        ORDER BY published_at DESC NULLS LAST
        """,
        (days,),
        dict_rows=True,
    )]


# --------------------------------------------------------------------------- #
# Prediction follow-through (T14)
# --------------------------------------------------------------------------- #
def insert_podcast_predictions(video_id: str, channel: str, published_at,
                               predictions: list[dict]) -> int:
    """Upsert one row per prediction (dedupe on (video_id, claim)). Called at
    summarize time; predictions: [{claim, entities: [slug,...]}]. Never overwrites an
    already-checked outcome (ON CONFLICT DO NOTHING). Returns rows inserted."""
    rows = [p for p in predictions if (p.get("claim") or "").strip()]
    if not rows:
        return 0
    with _conn() as conn, conn.cursor() as cur:
        n = 0
        for p in rows:
            cur.execute(
                """
                INSERT INTO podcast_predictions (video_id, channel, claim, entities, predicted_at)
                VALUES (%s, %s, %s, %s::jsonb, %s::timestamptz)
                ON CONFLICT (video_id, claim) DO NOTHING
                """,
                (video_id, channel, p["claim"].strip(),
                 json.dumps(p.get("entities") or []), published_at),
            )
            n += cur.rowcount
        return n


def get_open_predictions(min_age_days: int = 14, limit: int = 500) -> list[dict]:
    """Open predictions old enough to have plausibly played out (predicted_at ≤ now -
    min_age_days). Newest first. For the deterministic recheck."""
    return [dict(r) for r in _fetchall(
        """
        SELECT id, video_id, channel, claim, entities, predicted_at, created_at
        FROM podcast_predictions
        WHERE outcome = 'open'
          AND COALESCE(predicted_at, created_at) <= now() - make_interval(days => %s)
        ORDER BY COALESCE(predicted_at, created_at) DESC
        LIMIT %s
        """,
        (min_age_days, limit),
        dict_rows=True,
    )]


def update_prediction_outcome(pred_id: int, outcome: str, evidence: list[dict]) -> None:
    _execute(
        "UPDATE podcast_predictions SET outcome = %s, evidence = %s::jsonb, checked_at = now() "
        "WHERE id = %s",
        (outcome, json.dumps(evidence or []), pred_id),
    )


def get_predictions_for_research(limit: int = 40) -> list[dict]:
    """Corroborated + still-open predictions for a Research-page follow-through module.
    Corroborated (coverage appeared) first, then most recent. Read-only, deterministic."""
    return [dict(r) for r in _fetchall(
        """
        SELECT p.video_id, p.channel, p.claim, p.entities, p.predicted_at,
               p.outcome, p.evidence, p.checked_at, e.title AS episode_title, e.url
        FROM podcast_predictions p
        LEFT JOIN podcast_episodes e ON e.video_id = p.video_id
        WHERE p.outcome IN ('corroborated', 'open')
        ORDER BY (p.outcome = 'corroborated') DESC,
                 COALESCE(p.predicted_at, p.created_at) DESC
        LIMIT %s
        """,
        (limit,),
        dict_rows=True,
    )]
