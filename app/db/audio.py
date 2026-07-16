"""app.db.audio — audio briefings, OG cards, and mirrored entity avatars."""
from __future__ import annotations

from datetime import date as date_t

from ._core import _execute, _fetchall, _fetchone


# --------------------------------------------------------------------------- #
# Daily audio briefing (see app/briefing.py) — one rendered briefing per digest date
# --------------------------------------------------------------------------- #
def upsert_audio_briefing(digest_date: "date_t", variant: str = "standard", script: str = "",
                          audio: "bytes | None" = None,
                          mime: str = "audio/mpeg", voice: str = "", tts_engine: str = "",
                          duration_sec: "int | None" = None, byte_size: "int | None" = None,
                          word_count: "int | None" = None, model_used: str = "",
                          status: str = "pending", chapters: "list | None" = None,
                          waveform: "list | None" = None) -> None:
    """Upsert one length ``variant`` ('short'|'standard'|'explainer') of the audio briefing for a
    date. ``audio`` is raw bytes (stored as bytea) or None when synth hasn't succeeded yet;
    ``status`` carries the lifecycle (pending/ready/blocked/failed). ``chapters`` is an ordered
    [{title, start}] list (seconds) for in-player navigation, or None. ``waveform`` is a
    pseudo-waveform array (100 floats 0-1) for the web player bar visualisation."""
    import json as _json
    from psycopg2 import Binary  # lazy: only this path stores binary
    _execute(
        """
        INSERT INTO digest_audio
            (digest_date, variant, script, audio, mime, voice, tts_engine, duration_sec,
             byte_size, word_count, model_used, status, chapters, waveform, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, now())
        ON CONFLICT (digest_date, variant) DO UPDATE SET
            script       = EXCLUDED.script,
            audio        = EXCLUDED.audio,
            mime         = EXCLUDED.mime,
            voice        = EXCLUDED.voice,
            tts_engine   = EXCLUDED.tts_engine,
            duration_sec = EXCLUDED.duration_sec,
            byte_size    = EXCLUDED.byte_size,
            word_count   = EXCLUDED.word_count,
            model_used   = EXCLUDED.model_used,
            status       = EXCLUDED.status,
            chapters     = EXCLUDED.chapters,
            waveform     = EXCLUDED.waveform,
            created_at   = now()
        """,
        (digest_date, variant, script, Binary(audio) if audio is not None else None, mime, voice,
         tts_engine, duration_sec, byte_size, word_count, model_used, status,
         _json.dumps(chapters) if chapters else None,
         _json.dumps(waveform) if waveform else None),
    )


def delete_og_card(digest_date: "date_t") -> None:
    """Drop any cached OG card for a date so the next render is fresh (used before a re-warm)."""
    _execute("DELETE FROM digest_og WHERE digest_date = %s", (digest_date,))


def upsert_og_card(digest_date: "date_t", png: bytes, mime: str = "image/png",
                   source_at=None) -> None:
    """Store the pre-rendered /api/og card (PNG bytes) for a date. The public web route
    serves these bytes instead of rendering per-request. ``source_at`` is the digest
    created_at the card was rendered from (for staleness checks)."""
    from psycopg2 import Binary  # lazy: only this path stores binary
    _execute(
        """
        INSERT INTO digest_og (digest_date, png, mime, byte_size, source_at, created_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (digest_date) DO UPDATE SET
            png        = EXCLUDED.png,
            mime       = EXCLUDED.mime,
            byte_size  = EXCLUDED.byte_size,
            source_at  = EXCLUDED.source_at,
            created_at = now()
        """,
        (digest_date, Binary(png), mime, len(png), source_at),
    )


def upsert_entity_avatar(slug: str, image: bytes, source_url: str,
                         mime: str = "image/png", etag: "str | None" = None) -> None:
    """Store mirrored avatar bytes for an entity. The public /api/avatar/[slug] route serves
    these from our own DB instead of the browser hitting unavatar.io per node. See app/avatars.py."""
    from psycopg2 import Binary  # lazy: only this path stores binary
    _execute(
        """
        INSERT INTO entity_avatars (slug, image, mime, source_url, etag, byte_size, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (slug) DO UPDATE SET
            image      = EXCLUDED.image,
            mime       = EXCLUDED.mime,
            source_url = EXCLUDED.source_url,
            etag       = EXCLUDED.etag,
            byte_size  = EXCLUDED.byte_size,
            fetched_at = now()
        """,
        (slug, Binary(image), mime, source_url, etag, len(image)),
    )


def get_audio_briefing(digest_date: "date_t", variant: str = "standard") -> "dict | None":
    """Metadata for one length ``variant`` of the audio briefing on a date (NO audio bytes — use
    get_audio_bytes), or None. Telegram + legacy callers use the default 'standard' variant."""
    row = _fetchone(
        """SELECT digest_date, variant, mime, voice, tts_engine, duration_sec, byte_size,
                  word_count, model_used, status, chapters, (audio IS NOT NULL) AS has_audio,
                  created_at
           FROM digest_audio WHERE digest_date = %s AND variant = %s""",
        (digest_date, variant),
        dict_rows=True,
    )
    return dict(row) if row else None


def get_audio_bytes(digest_date: "date_t", variant: str = "standard") -> "bytes | None":
    """The rendered audio bytes for one length ``variant`` of a date, or None if absent."""
    row = _fetchone("SELECT audio FROM digest_audio WHERE digest_date = %s AND variant = %s",
                    (digest_date, variant))
    if not row or row[0] is None:
        return None
    return bytes(row[0])


def get_existing_audio_variants(digest_date: "date_t") -> set:
    """The set of audio-briefing variant keys already stored for a date, EXCLUDING 'failed'
    rows. Lets a backfill render only the MISSING variants instead of re-running existing
    ones. A 'failed' row is a retryable marker (script couldn't be produced — see
    app/briefing.py), so it counts as missing; 'blocked' is a terminal fail-closed verdict
    and 'pending'/'ready' are in-flight/done — none of those are re-rendered."""
    return {r[0] for r in _fetchall(
        "SELECT variant FROM digest_audio WHERE digest_date = %s AND status <> 'failed'",
        (digest_date,))}


def get_audio_variant_status(digest_date: "date_t") -> list[dict]:
    """Per-variant lifecycle for a date: ``[{variant, status, created_at}]`` for every stored
    row (any status). Drives the same-day AUDIO self-heal (``briefing.variants_needing_heal`` +
    ``main._maybe_heal_audio``), which needs each variant's status AND last-attempt time to decide
    whether a 'failed' row is old enough to retry. Empty list = no rows yet for the date."""
    return _fetchall(
        "SELECT variant, status, created_at FROM digest_audio WHERE digest_date = %s",
        (digest_date,),
        dict_rows=True,
    )


def get_digest_dates_without_audio(variants: "tuple[str, ...]" = ("standard",)) -> list["date_t"]:
    """Digest dates that have bullet analyses but are missing one or more of the given audio
    ``variants`` (for backfill). A date counts as "complete" once it has a non-'failed' row
    for every requested variant — an intentionally blocked render isn't retried forever, but
    a 'failed' marker (script never produced) stays visible to backfill."""
    return [r[0] for r in _fetchall(
        """
        SELECT a.digest_date
        FROM (SELECT DISTINCT digest_date FROM digest_bullet_analysis) a
        LEFT JOIN (
            SELECT digest_date, count(*) AS n
            FROM digest_audio
            WHERE variant = ANY(%s) AND status <> 'failed'
            GROUP BY digest_date
        ) au ON au.digest_date = a.digest_date
        WHERE coalesce(au.n, 0) < %s
        ORDER BY a.digest_date
        """,
        (list(variants), len(variants)),
    )]


def null_old_audio(days: int) -> int:
    """Retention: NULL the rendered audio BYTES on briefings older than ``days``; the script,
    chapters, waveform, and metadata are kept forever (transcript history survives — only the
    ~2 MB/variant bytea goes). digest_audio is the DB's largest growth line (294 MB, 44% of
    the DB, on 2026-07-07 at ~5 MB/day); this bounds it. Aged-out rows keep status='ready',
    so the backfill queries above still count them as complete and never re-render them; the
    web route already treats absent audio as absent. Returns the number of rows nulled."""
    if days <= 0:
        return 0
    return _execute(
        "UPDATE digest_audio SET audio = NULL, byte_size = NULL "
        "WHERE digest_date < current_date - %s AND audio IS NOT NULL",
        (days,),
    )
