"""app.db.feeds — RSS feed ingestion, embeddings, source health, and feed-item reads/search."""
from __future__ import annotations

from datetime import date as date_t
import logging

from psycopg2.extras import RealDictCursor, execute_values

from .. import config, embeddings
from ._core import _conn, _execute, _fetchall, _fetchone, _vec_literal, _term_boundary_regex

log = logging.getLogger("app.db")


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
def insert_feed_items(items: list[dict]) -> tuple[int, set[str]]:
    """Insert rows; dedupe on the UNIQUE(link).

    Returns (inserted_count, inserted_links) so callers can pass only the
    genuinely new items to downstream steps (entity extraction, etc.) rather
    than re-processing the full batch.

    Accepts optional ``title`` and ``quality_flag`` keys (added by the
    title_content_coherence_check gate in ingest.py). Ignored gracefully if the
    columns have not yet been migrated (schema migration is additive/optional).
    """
    if not items:
        return 0, set()

    # Detect whether the schema has the new columns by checking the first item's keys.
    # Once the migration is applied the columns are always present; before that we fall
    # back to the legacy 7-column insert.
    has_extended = "title" in items[0] and "quality_flag" in items[0]

    if has_extended:
        rows = [
            (it["link"], it.get("title") or "", it["content"], it["creator"],
             it["pub_date"], it["source_type"], it["metadata"],
             it.get("mentions") or [], it.get("quality_flag") or "ok")
            for it in items
        ]
        sql = """
            INSERT INTO feed_items
              (link, title, content, creator, pub_date, source_type, metadata, mentions, quality_flag)
            VALUES %s
            ON CONFLICT DO NOTHING
            RETURNING link
        """
        tmpl = "(%s, %s, %s, %s, %s::timestamptz, %s, %s::jsonb, %s::text[], %s)"
    else:
        rows = [
            (it["link"], it["content"], it["creator"], it["pub_date"],
             it["source_type"], it["metadata"], it.get("mentions") or [])
            for it in items
        ]
        sql = """
            INSERT INTO feed_items (link, content, creator, pub_date, source_type, metadata, mentions)
            VALUES %s
            ON CONFLICT DO NOTHING
            RETURNING link
        """
        tmpl = "(%s, %s, %s, %s::timestamptz, %s, %s::jsonb, %s::text[])"

    try:
        with _conn() as conn, conn.cursor() as cur:
            result = execute_values(cur, sql, rows, template=tmpl, fetch=True)
        inserted_links = {row[0] for row in result}
        return len(inserted_links), inserted_links
    except Exception as exc:
        # If the extended insert fails (column doesn't exist yet), fall back to
        # the legacy path so the ingest cycle is not broken pre-migration.
        if has_extended and "column" in str(exc).lower():
            log.warning(
                "db: title/quality_flag columns missing — run schema migration. "
                "Falling back to legacy insert."
            )
            rows_legacy = [
                (it["link"], it["content"], it["creator"], it["pub_date"],
                 it["source_type"], it["metadata"], it.get("mentions") or [])
                for it in items
            ]
            with _conn() as conn, conn.cursor() as cur:
                result = execute_values(
                    cur,
                    """
                    INSERT INTO feed_items (link, content, creator, pub_date, source_type, metadata, mentions)
                    VALUES %s ON CONFLICT DO NOTHING RETURNING link
                    """,
                    rows_legacy,
                    template="(%s, %s, %s, %s::timestamptz, %s, %s::jsonb, %s::text[])",
                    fetch=True,
                )
            inserted_links = {row[0] for row in result}
            return len(inserted_links), inserted_links
        raise


def existing_links(links) -> set[str]:
    """Return the subset of ``links`` already present in feed_items.

    Used by non-RSS scrapers (e.g. app.kaiko) to skip re-fetching article pages
    that have already been ingested — the per-link UNIQUE makes re-insert a no-op,
    but the network fetch is the expensive part we want to avoid each cron tick.
    """
    links = list(links)
    if not links:
        return set()
    rows = _fetchall("SELECT link FROM feed_items WHERE link = ANY(%s)", (links,))
    return {r[0] for r in rows}


def record_ingest_run(started_at, raw, cleaned, inserted, embedded,
                      sources_ok, sources_failed, duration_ms) -> None:
    _execute(
        "INSERT INTO ingest_run (started_at, raw, cleaned, inserted, embedded, "
        "sources_ok, sources_failed, duration_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (started_at, raw, cleaned, inserted, embedded, sources_ok, sources_failed, duration_ms),
    )


def update_source_health(results: list[dict]) -> None:
    """Upsert latest per-source fetch outcome; track consecutive failures."""
    rows = [
        (r["url"], r["ok"], r.get("status"), r.get("item_count", 0), r.get("error"), r["ok"])
        for r in results
    ]
    with _conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO source_health
              (url, last_ok, last_status, last_item_count, last_error, consecutive_failures, updated_at)
            VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN 0 ELSE 1 END, now())
            ON CONFLICT (url) DO UPDATE SET
              last_ok = EXCLUDED.last_ok,
              last_status = EXCLUDED.last_status,
              last_item_count = EXCLUDED.last_item_count,
              last_error = EXCLUDED.last_error,
              consecutive_failures = CASE WHEN EXCLUDED.last_ok
                THEN 0 ELSE source_health.consecutive_failures + 1 END,
              updated_at = now()
            """,
            rows,
        )


def count_missing_embeddings() -> int:
    return _fetchone("SELECT count(*) FROM feed_items WHERE embedding IS NULL")[0]


def _embed_batch(batch: list[tuple]) -> int:
    """Embed cleaned content for (id, content) rows, then store.

    Embeddings run in ONE batched /embeddings request per EMBED_BATCH_SIZE chunk (no DB
    connection held); only the UPDATEs take a connection. Stored corpus embeds as PASSAGE.
    A whole-batch API failure (after in-call retries) is swallowed so the run continues — the
    rows stay NULL and re-embed on the next ingest self-heal cycle. Returns rows updated.
    """
    cleaned = [(row_id, embeddings.clean_for_embedding(content)) for row_id, content in batch]
    rows = [(row_id, text) for row_id, text in cleaned if text]
    if not rows:
        return 0
    try:
        vecs = embeddings.embed_batch([text for _, text in rows], input_type=embeddings.PASSAGE)
    except Exception:
        log.exception("embedding batch failed for %d rows (will retry next cycle)", len(rows))
        return 0
    updates = [(_vec_literal(vec), embeddings.EMBED_VERSION, row_id)
               for (row_id, _), vec in zip(rows, vecs)]
    if not updates:
        return 0
    with _conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE feed_items SET embedding = %s::vector, embed_version = %s WHERE id = %s",
            updates,
        )
    return len(updates)


def _embed_where(where: str, batch_size: int, on_batch=None) -> int:
    """Embed rows matching ``where`` (must reference id), walking by id cursor so
    un-embeddable rows can't cause an infinite loop. Cleaned text is embedded."""
    total, last_id = 0, 0
    while True:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id, content FROM feed_items "
                f"WHERE ({where}) AND id > %s AND content IS NOT NULL AND length(content) > 0 "
                f"ORDER BY id LIMIT %s",
                (last_id, batch_size),
            )
            batch = cur.fetchall()
        if not batch:
            break
        last_id = batch[-1][0]
        total += _embed_batch(batch)
        if on_batch:
            on_batch(total, last_id)
    return total


def embed_missing(batch_size: int = 200) -> int:
    """Embed rows with a NULL embedding (new rows). Self-healing; sets embed_version."""
    return _embed_where("embedding IS NULL", batch_size)


def reembed_stale(batch_size: int = 100, on_batch=None) -> int:
    """Re-embed rows whose embed_version is behind the current cleaning logic.

    Updates embeddings IN PLACE (old vectors stay searchable until overwritten),
    so there is no search downtime. Resumable — re-running continues the backlog.
    """
    return _embed_where(f"embed_version < {embeddings.EMBED_VERSION}", batch_size, on_batch)


def count_stale_embeddings() -> int:
    return _fetchone("SELECT count(*) FROM feed_items WHERE embed_version < %s",
                     (embeddings.EMBED_VERSION,))[0]


# --------------------------------------------------------------------------- #
# Digest
# --------------------------------------------------------------------------- #
def _fetchall_quality(sql: str, legacy_sql: str, params) -> list[dict]:
    """Run a feed SELECT that reads ``quality_flag``; degrade to the legacy variant
    (every row flagged 'ok') when the column has not been migrated yet."""
    try:
        return _fetchall(sql, params, dict_rows=True)
    except Exception as exc:
        if "quality_flag" not in str(exc):
            raise
        rows = _fetchall(legacy_sql, params, dict_rows=True)
        for r in rows:
            r["quality_flag"] = "ok"
        return rows


def get_recent_feed_items(hours: int, limit: int) -> list[dict]:
    """Return feed items whose effective publication date falls within the last `hours` hours.

    Uses COALESCE(pub_date, ingested_at) as the canonical timestamp — pub_date is always
    set on new items (sanitize_items falls back to now()); NULL only exists for rows
    predating that guarantee, where ingested_at is the next-best approximation.

    This query hits the existing functional index on COALESCE(pub_date, ingested_at) DESC
    and naturally excludes re-syndicated stale content (old pub_date → outside the window).
    Monitoring queries that need ingest-time counts use ingested_at directly.

    Returns ``quality_flag`` so digest._format_items can annotate thin/boilerplate
    items in the LLM prompt ('ok' for all rows pre-migration).
    """
    where = "COALESCE(pub_date, ingested_at) >= now() - make_interval(hours => %s)"
    order = "ORDER BY COALESCE(pub_date, ingested_at) DESC LIMIT %s"
    return _fetchall_quality(
        f"""
        SELECT content, link, creator, pub_date, source_type,
               COALESCE(quality_flag, 'ok') AS quality_flag
        FROM feed_items WHERE {where} {order}
        """,
        f"SELECT content, link, creator, pub_date, source_type FROM feed_items WHERE {where} {order}",
        (hours, limit),
    )


def get_feed_items_for_date(target_date: date_t, limit: int = 200) -> list[dict]:
    """Return feed items PUBLISHED on target_date (UTC), falling back to ingest time.

    Keyed on COALESCE(pub_date, ingested_at) so a backfilled digest reflects the news
    published that day — not when the scraper happened to ingest it. Startup bulk-seed data
    can have ingested_at lag pub_date by a day+ (e.g. 2026-05-11 items ingested 2026-05-12),
    which made an ingested_at-keyed query return nothing for the earliest backfill date.

    Returns ``quality_flag`` (see get_recent_feed_items) for the backfill digest path.
    """
    where = ("COALESCE(pub_date, ingested_at) >= %s::date "
             "AND COALESCE(pub_date, ingested_at) < %s::date + interval '1 day'")
    order = "ORDER BY COALESCE(pub_date, ingested_at) DESC LIMIT %s"
    return _fetchall_quality(
        f"""
        SELECT content, link, creator, pub_date, source_type,
               COALESCE(quality_flag, 'ok') AS quality_flag
        FROM feed_items WHERE {where} {order}
        """,
        f"SELECT content, link, creator, pub_date, source_type FROM feed_items WHERE {where} {order}",
        (target_date, target_date, limit),
    )


def get_feed_items_since(days: int, limit: int = 40000) -> list[dict]:
    """Feed items from the last `days` days (by pub_date, fallback ingested_at) — for the
    entity co-occurrence graph. Returns content + ts only, newest first."""
    return _fetchall(
        """
        SELECT content, link, COALESCE(pub_date, ingested_at) AS ts
        FROM feed_items
        WHERE COALESCE(pub_date, ingested_at) >= now() - make_interval(days => %s)
          AND content IS NOT NULL AND content <> ''
        ORDER BY COALESCE(pub_date, ingested_at) DESC
        LIMIT %s
        """,
        (days, limit),
        dict_rows=True,
    )


def get_feed_items_since_ts(since_ts, limit: int = 200) -> list[dict]:
    """Feed items whose effective publication time (COALESCE(pub_date, ingested_at)) is
    at or after `since_ts` — the intraday-update window (app/intraday.py). Same column
    shape as get_recent_feed_items so digest._format_items can consume it directly.

    NOT to be confused with get_feed_items_since(days, ...) above, which takes a day count
    and returns a lean content+ts shape for the entity graph.
    """
    where = "COALESCE(pub_date, ingested_at) >= %s"
    order = "ORDER BY COALESCE(pub_date, ingested_at) DESC LIMIT %s"
    return _fetchall_quality(
        f"""
        SELECT content, link, creator, pub_date, source_type,
               COALESCE(quality_flag, 'ok') AS quality_flag
        FROM feed_items WHERE {where} {order}
        """,
        f"SELECT content, link, creator, pub_date, source_type FROM feed_items WHERE {where} {order}",
        (since_ts, limit),
    )


def get_recent_unalerted_items(minutes: int, max_age_hours: int = 48,
                               limit: int = 400) -> list[dict]:
    """Feed items ingested in the last `minutes` minutes that have NOT already been alerted
    (app/alerts.py, off the 20-min ingest cycle). Anti-joined against alerts_sent on the
    (already-normalized) link. `max_age_hours` also drops re-syndicated stale content whose
    pub_date is old even though it was just ingested — breaking-news alerts want fresh events.

    Returns title/content/link/creator/source_type/quality_flag plus the effective `ts`.
    """
    return _fetchall(
        """
        SELECT f.title, f.content, f.link, f.creator, f.source_type,
               COALESCE(f.quality_flag, 'ok') AS quality_flag,
               COALESCE(f.pub_date, f.ingested_at) AS ts
        FROM feed_items f
        LEFT JOIN alerts_sent a ON a.link = f.link
        WHERE a.link IS NULL
          AND f.ingested_at >= now() - make_interval(mins => %s)
          AND COALESCE(f.pub_date, f.ingested_at) >= now() - make_interval(hours => %s)
          AND f.content IS NOT NULL AND f.content <> ''
        ORDER BY COALESCE(f.pub_date, f.ingested_at) DESC
        LIMIT %s
        """,
        (minutes, max_age_hours, limit),
        dict_rows=True,
    )


def get_source_ingestion_counts() -> list[tuple]:
    """Items ingested per source, derived from feed item links.

    Returns (source_type, source_key, total, last_7d, last_24h, last_item_at) rows where:
    - twitter source_key = lowercase handle extracted from x.com/twitter.com/nitter.net link
    - news source_key    = domain extracted from article link
    """
    return _fetchall("""
        SELECT
          source_type,
          CASE
            WHEN source_type = 'twitter'
              THEN lower(regexp_replace(link,
                'https?://(?:x\\.com|twitter\\.com|nitter\\.net)/([^/]+)/.*', '\\1'))
            ELSE regexp_replace(link,
                'https?://(?:www\\.)?([^/?#]+).*', '\\1')
          END                                                               AS source_key,
          COUNT(*)                                                          AS total,
          COUNT(*) FILTER (WHERE ingested_at >= now()-'7 days'::interval)  AS last_7d,
          COUNT(*) FILTER (WHERE ingested_at >= now()-'24 hours'::interval) AS last_24h,
          MAX(ingested_at)                                                  AS last_item_at
        FROM feed_items
        GROUP BY 1, 2
    """)


def get_feed_items_matching_terms(terms: list[str], around_date: "date_t",
                                  window_hours: int = 24) -> list[dict]:
    """Return feed items whose content word-boundary-matches any of `terms`, within a
    `window_hours` window ending at the end of `around_date` (so it works for both the
    live digest and historical backfill). Used for corroboration + velocity signals.

    Returns ``quality_flag`` when the column exists (post-migration); falls back
    to ``'ok'`` for all rows if the column is absent (pre-migration).
    """
    pattern = _term_boundary_regex(terms)
    if not pattern:
        return []
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute(
                """
                SELECT link, content, COALESCE(pub_date, ingested_at) AS ts,
                       COALESCE(quality_flag, 'ok') AS quality_flag
                FROM feed_items
                WHERE content ~* %s
                  AND COALESCE(pub_date, ingested_at) <  %s::date + interval '1 day'
                  AND COALESCE(pub_date, ingested_at) >= %s::date + interval '1 day'
                                                        - make_interval(hours => %s)
                ORDER BY ts ASC
                LIMIT 200
                """,
                (pattern, around_date, around_date, window_hours),
            )
            return cur.fetchall()
        except Exception as exc:
            if "quality_flag" in str(exc):
                # Column not yet migrated — fall back to the legacy query.
                conn.rollback()
                cur.execute(
                    """
                    SELECT link, content, COALESCE(pub_date, ingested_at) AS ts
                    FROM feed_items
                    WHERE content ~* %s
                      AND COALESCE(pub_date, ingested_at) <  %s::date + interval '1 day'
                      AND COALESCE(pub_date, ingested_at) >= %s::date + interval '1 day'
                                                            - make_interval(hours => %s)
                    ORDER BY ts ASC
                    LIMIT 200
                    """,
                    (pattern, around_date, around_date, window_hours),
                )
                rows = cur.fetchall()
                for r in rows:
                    r["quality_flag"] = "ok"
                return rows
            raise


def get_kaiko_feed_signals(days: int) -> list[dict]:
    """Return Kaiko Research articles for the narrative signal pipeline.

    Pulled directly from feed_items (bypassing the digest-bullet step) because Kaiko
    publishes analysis/research, not event-driven news — most articles never become
    digest bullets even though they carry strong thematic signal for the Research layer.
    """
    return _fetchall(
        """
        SELECT COALESCE(title, '') AS title, content, link,
               COALESCE(pub_date, ingested_at) AS ts
        FROM feed_items
        WHERE creator = 'Kaiko'
          AND COALESCE(pub_date, ingested_at) >= now() - make_interval(days => %s)
          AND content IS NOT NULL AND length(content) > 100
        ORDER BY COALESCE(pub_date, ingested_at) DESC
        """,
        (days,),
        dict_rows=True,
    )


def get_chronic_failing_sources(min_failures: int = 5) -> list[tuple[str, int]]:
    """Return (url, consecutive_failures) for sources that have been failing persistently."""
    return _fetchall(
        "SELECT url, consecutive_failures FROM source_health "
        "WHERE consecutive_failures >= %s ORDER BY consecutive_failures DESC",
        (min_failures,),
    )


# --------------------------------------------------------------------------- #
def search_feed(keyword: str, topk: int | None = None, days: int | None = None) -> list[dict]:
    """Semantic feed search for LLM grounding (agent tool, related coverage, entity briefs).

    Excludes ``thin_content`` rows: a <80-char teaser ("Learn more: …", "Source: …")
    carries no reportable substance, and injecting one as "grounding" invites the model
    to confabulate what sits behind the link. Falls back to the unfiltered query when
    the quality_flag column has not been migrated yet.
    """
    topk = topk or config.SEARCH_TOPK
    days = days or config.SEARCH_WINDOW_DAYS
    qvec = _vec_literal(embeddings.embed(embeddings.clean_for_embedding(keyword) or keyword,
                                         input_type=embeddings.QUERY))

    def _run(quality_filter: str) -> list[dict]:
        with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL ivfflat.probes = %s", (config.IVFFLAT_PROBES,))
            cur.execute(
                f"""
                SELECT content, link, creator, pub_date, source_type,
                       1 - (embedding <=> %s::vector) AS score
                FROM feed_items
                WHERE embedding IS NOT NULL
                  AND COALESCE(pub_date, ingested_at) >= now() - make_interval(days => %s)
                  {quality_filter}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (qvec, days, qvec, topk),
            )
            return cur.fetchall()

    try:
        return _run("AND COALESCE(quality_flag, 'ok') <> 'thin_content'")
    except Exception as exc:
        if "quality_flag" not in str(exc):
            raise
        return _run("")
