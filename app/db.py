"""Postgres data layer.

pgvector values are passed as text literals cast with ``::vector`` so no extra
adapter package is required.
"""
from __future__ import annotations

import contextlib
import json
import logging
from datetime import date as date_t

from psycopg2 import pool as pgpool
from psycopg2.extras import RealDictCursor, execute_values

from . import config, embeddings

log = logging.getLogger(__name__)

_pool = pgpool.ThreadedConnectionPool(minconn=1, maxconn=8, dsn=config.DATABASE_URL)


@contextlib.contextmanager
def _conn():
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
def insert_feed_items(items: list[dict]) -> tuple[int, set[str]]:
    """Insert rows; dedupe on the UNIQUE(link).

    Returns (inserted_count, inserted_links) so callers can pass only the
    genuinely new items to downstream steps (entity extraction, etc.) rather
    than re-processing the full batch.
    """
    if not items:
        return 0, set()
    rows = [
        (it["link"], it["content"], it["creator"], it["pub_date"],
         it["source_type"], it["metadata"], it.get("mentions") or [])
        for it in items
    ]
    with _conn() as conn, conn.cursor() as cur:
        result = execute_values(
            cur,
            """
            INSERT INTO feed_items (link, content, creator, pub_date, source_type, metadata, mentions)
            VALUES %s
            ON CONFLICT DO NOTHING
            RETURNING link
            """,
            rows,
            template="(%s, %s, %s, %s::timestamptz, %s, %s::jsonb, %s::text[])",
            fetch=True,
        )
    inserted_links = {row[0] for row in result}
    return len(inserted_links), inserted_links


def record_ingest_run(started_at, raw, cleaned, inserted, embedded,
                      sources_ok, sources_failed, duration_ms) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
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
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM feed_items WHERE embedding IS NULL")
        return cur.fetchone()[0]


def _embed_batch(batch: list[tuple]) -> int:
    """Embed cleaned content for (id, content) rows, then store.

    Embeddings (slow network calls) run with NO DB connection held; only the
    UPDATEs take a connection. Returns rows updated.
    """
    updates: list[tuple] = []
    for row_id, content in batch:
        text = embeddings.clean_for_embedding(content)
        if not text:
            continue
        try:
            vec = embeddings.embed(text)
        except Exception:
            log.exception("embedding failed for feed_items.id=%s", row_id)
            continue
        updates.append((_vec_literal(vec), embeddings.EMBED_VERSION, row_id))
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
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM feed_items WHERE embed_version < %s",
                    (embeddings.EMBED_VERSION,))
        return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Digest
# --------------------------------------------------------------------------- #
def get_recent_feed_items(hours: int, limit: int) -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT content, link, creator, pub_date, source_type
            FROM feed_items
            WHERE ingested_at >= now() - make_interval(hours => %s)
            ORDER BY ingested_at DESC
            LIMIT %s
            """,
            (hours, limit),
        )
        return cur.fetchall()


def get_feed_items_for_date(target_date: date_t, limit: int = 200) -> list[dict]:
    """Return feed items PUBLISHED on target_date (UTC), falling back to ingest time.

    Keyed on COALESCE(pub_date, ingested_at) so a backfilled digest reflects the news
    published that day — not when the scraper happened to ingest it. Startup bulk-seed data
    can have ingested_at lag pub_date by a day+ (e.g. 2026-05-11 items ingested 2026-05-12),
    which made an ingested_at-keyed query return nothing for the earliest backfill date.
    """
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT content, link, creator, pub_date, source_type
            FROM feed_items
            WHERE COALESCE(pub_date, ingested_at) >= %s::date
              AND COALESCE(pub_date, ingested_at) <  %s::date + interval '1 day'
            ORDER BY COALESCE(pub_date, ingested_at) DESC
            LIMIT %s
            """,
            (target_date, target_date, limit),
        )
        return cur.fetchall()


def get_cache() -> tuple[object | None, str]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT last_run, last_analysis FROM crypto_cache LIMIT 1")
        row = cur.fetchone()
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
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO crypto_digest (created_at, date, content, model_used, trigger, duration_ms, error)"
            " VALUES (now(), %s, %s, %s, %s, %s, %s)",
            (d, content, model_used, trigger, duration_ms, error),
        )


def record_keyword_analysis(keyword: str, chat_id: str, model_used: str,
                             duration_ms: int | None = None) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO keyword_analysis (keyword, chat_id, model_used, duration_ms)"
            " VALUES (%s, %s, %s, %s)",
            (keyword[:500], chat_id, model_used, duration_ms),
        )


def get_digest(d: "date_t") -> "dict | None":
    """Return the most recent error-free digest for a date as a dict, or None."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT date, content, model_used, trigger
               FROM crypto_digest
               WHERE date = %s AND error IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (d,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"date": row[0], "content": row[1], "model_used": row[2], "trigger": row[3]}


def get_source_ingestion_counts() -> list[tuple]:
    """Items ingested per source, derived from feed item links.

    Returns (source_type, source_key, total, last_7d, last_24h, last_item_at) rows where:
    - twitter source_key = lowercase handle extracted from x.com/twitter.com/nitter.net link
    - news source_key    = domain extracted from article link
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
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
        return cur.fetchall()


def get_recent_digests(limit: int = 15) -> list[tuple]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT created_at, date, model_used, trigger, duration_ms, error
               FROM crypto_digest ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        return cur.fetchall()


def upsert_tvl(rows: list[tuple]) -> None:
    """Upsert (date, chain, tvl_usd) rows into defillama_tvl."""
    with _conn() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO defillama_tvl (date, chain, tvl_usd)
            VALUES %s
            ON CONFLICT (date, chain) DO UPDATE
              SET tvl_usd = EXCLUDED.tvl_usd, fetched_at = now()
            """,
            rows,
            template="(%s, %s, %s)",
        )


def get_latest_tvl() -> list[tuple]:
    """Return the most recent (date, chain, tvl_usd) per chain, ordered by tvl_usd DESC."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (chain) date, chain, tvl_usd
            FROM defillama_tvl
            ORDER BY chain, date DESC
            """
        )
        rows = cur.fetchall()
    # sort: total first, then by tvl desc
    return sorted(rows, key=lambda r: (r[1] != "total", -r[2]))


def get_chain_tvl_for_week(week_end: date_t) -> dict:
    """Reconstruct chain TVL for a (past) week from the defillama_tvl time-series.

    Returns {chain: {"tvl": usd, "change_7d": pct|None, "as_of": date}} using the latest
    snapshot on or before ``week_end``, with WoW change vs the latest snapshot ~7d earlier.
    Empty dict if no snapshot exists in range (week predates the daily TVL cron). Used by the
    weekly backfill to recover DeFi data from the DB instead of emitting "data unavailable".
    """
    from datetime import timedelta
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(date) FROM defillama_tvl WHERE date <= %s", (week_end,))
        cur_date = cur.fetchone()[0]
        if not cur_date:
            return {}
        cur.execute("SELECT chain, tvl_usd FROM defillama_tvl WHERE date = %s", (cur_date,))
        cur_rows = {c: t for c, t in cur.fetchall()}
        cur.execute("SELECT max(date) FROM defillama_tvl WHERE date <= %s",
                    (cur_date - timedelta(days=7),))
        prior_date = cur.fetchone()[0]
        prior_rows: dict = {}
        if prior_date and prior_date != cur_date:
            cur.execute("SELECT chain, tvl_usd FROM defillama_tvl WHERE date = %s", (prior_date,))
            prior_rows = {c: t for c, t in cur.fetchall()}
    out: dict = {}
    for chain, tvl in cur_rows.items():
        prev = prior_rows.get(chain)
        chg = ((tvl - prev) / prev * 100.0) if prev else None
        out[chain] = {"tvl": tvl, "change_7d": chg, "as_of": cur_date}
    return out


def upsert_protocols(rows: list[dict]) -> None:
    """Upsert protocol TVL rows into defillama_protocols on slug conflict."""
    if not rows:
        return
    with _conn() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO defillama_protocols
              (slug, name, category, chains, chain_tvls, tvl_usd, tvl_change_1d, tvl_change_7d,
               mcap_tvl, token_symbol, logo_url, url, description, gecko_id)
            VALUES %s
            ON CONFLICT (slug) DO UPDATE SET
              name          = EXCLUDED.name,
              category      = EXCLUDED.category,
              chains        = EXCLUDED.chains,
              chain_tvls    = EXCLUDED.chain_tvls,
              tvl_usd       = EXCLUDED.tvl_usd,
              tvl_change_1d = EXCLUDED.tvl_change_1d,
              tvl_change_7d = EXCLUDED.tvl_change_7d,
              mcap_tvl      = EXCLUDED.mcap_tvl,
              token_symbol  = EXCLUDED.token_symbol,
              logo_url      = EXCLUDED.logo_url,
              url           = EXCLUDED.url,
              description   = EXCLUDED.description,
              gecko_id      = EXCLUDED.gecko_id,
              fetched_at    = now()
            """,
            [(r["slug"], r["name"], r["category"], r["chains"],
              json.dumps(r.get("chain_tvls") or {}),
              r["tvl_usd"],
              r.get("tvl_change_1d"), r.get("tvl_change_7d"), r.get("mcap_tvl"),
              r["token_symbol"], r["logo_url"], r["url"], r["description"],
              r.get("gecko_id", ""))
             for r in rows],
            template="(%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        )


def get_top_protocols(limit: int = 20) -> list[tuple]:
    """Return top protocols by TVL: (name, category, chains, tvl_usd,
    tvl_change_1d, tvl_change_7d, url, token_symbol)."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT name, category, chains, tvl_usd,
                      tvl_change_1d, tvl_change_7d, url, token_symbol, gecko_id
               FROM defillama_protocols
               ORDER BY tvl_usd DESC
               LIMIT %s""",
            (limit,),
        )
        return cur.fetchall()


def get_recent_keyword_analyses(limit: int = 15) -> list[tuple]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT created_at, keyword, model_used, duration_ms
               FROM keyword_analysis ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        return cur.fetchall()


def get_protocols_by_slugs(slugs: list[str]) -> list[dict]:
    """Return TVL + metadata from defillama_protocols for the given slugs."""
    if not slugs:
        return []
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT slug, name, category, tvl_usd, tvl_change_1d, tvl_change_7d
               FROM defillama_protocols WHERE slug = ANY(%s)""",
            (slugs,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------- #
# Entity memory
# --------------------------------------------------------------------------- #
_ERC_RE = __import__("re").compile(
    r"^(ERC|EIP|BIP|CIP|AIP|SIP|RIP)-\d+$", __import__("re").IGNORECASE
)

def upsert_entity(slug: str, name: str, type_: str, aliases: list[str],
                  last_mentioned: "date_t | None" = None,
                  twitter_handle: str | None = None) -> None:
    """Insert or merge an entity. Aliases are union-merged; summary never overwritten.
    twitter_handle and logo_url are only updated when a non-None value is provided.
    """
    # Token/governance standards are specifications, not entities.
    if _ERC_RE.match(name):
        return
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity_memory (slug, name, type, aliases, last_mentioned, mention_count, updated_at, twitter_handle)
            VALUES (%s, %s, %s, %s, %s, 1, now(), %s)
            ON CONFLICT (slug) DO UPDATE SET
                name           = EXCLUDED.name,
                type           = EXCLUDED.type,
                aliases        = ARRAY(
                    SELECT DISTINCT unnest(entity_memory.aliases || EXCLUDED.aliases)
                ),
                last_mentioned = GREATEST(entity_memory.last_mentioned, EXCLUDED.last_mentioned),
                mention_count  = entity_memory.mention_count + 1,
                updated_at     = now(),
                twitter_handle = COALESCE(EXCLUDED.twitter_handle, entity_memory.twitter_handle)
            """,
            (slug, name, type_, aliases, last_mentioned, twitter_handle),
        )


def upsert_entity_from_coingecko(slug: str, name: str, type_: str,
                                  aliases: list[str], logo_url: str | None) -> None:
    """Seed-only upsert from CoinGecko.

    On INSERT: creates the entity with mention_count=1 (below the display threshold
    of 2, so it's invisible until organically mentioned in a feed).
    On CONFLICT: only fills in missing logo_url and merges new aliases.
    Never overwrites type, name, mention_count, or twitter_handle of existing entries.
    """
    if _ERC_RE.match(name):
        return
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity_memory (slug, name, type, aliases, mention_count, updated_at, logo_url)
            VALUES (%s, %s, %s, %s, 1, now(), %s)
            ON CONFLICT (slug) DO UPDATE SET
                aliases    = ARRAY(
                    SELECT DISTINCT unnest(entity_memory.aliases || EXCLUDED.aliases)
                ),
                updated_at = now(),
                logo_url   = COALESCE(entity_memory.logo_url, EXCLUDED.logo_url)
            """,
            (slug, name, type_, aliases, logo_url),
        )


def update_entity_summary(slug: str, summary: str) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE entity_memory SET summary = %s, updated_at = now() WHERE slug = %s",
            (summary, slug),
        )


def get_all_entity_aliases() -> list[tuple]:
    """Return (slug, name, type, aliases, summary) for every entity, hottest first."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT slug, name, type, aliases, summary"
            " FROM entity_memory ORDER BY mention_count DESC"
        )
        return cur.fetchall()


def get_entities_by_slugs(slugs: list[str]) -> list[dict]:
    if not slugs:
        return []
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT slug, name, type, summary, twitter_handle FROM entity_memory WHERE slug = ANY(%s)",
            (slugs,),
        )
        return cur.fetchall()


def get_governance_for_entity(slug: str, name: str, limit: int = 2) -> list[dict]:
    """Recent Snapshot governance proposals for an entity, newest-ending first.

    Matched by Snapshot space (``space_id`` like 'aave.eth' starts with the slug) or
    by ``space_name``. Returns [{title, state}] — best-effort ground-truth context for
    bullet analysis (callers wrap in try/except; an empty list is a valid result)."""
    if not slug and not name:
        return []
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT title, state FROM governance_proposals
               WHERE space_id ILIKE %s OR space_name ILIKE %s
               ORDER BY end_ts DESC NULLS LAST
               LIMIT %s""",
            (f"{slug}.%", f"%{name}%", limit),
        )
        return cur.fetchall()


def touch_entity_mentions(slugs: list[str], mention_date: "date_t") -> None:
    if not slugs:
        return
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE entity_memory
               SET last_mentioned = GREATEST(last_mentioned, %s),
                   mention_count  = mention_count + 1,
                   updated_at     = now()
               WHERE slug = ANY(%s)""",
            (mention_date, slugs),
        )


def decay_stale_entities() -> int:
    """Decay inactive entity mention counts and prune stale 'other' entities.

    Decays mention_count by 20% for entities not mentioned in the last 14 days.
    Deletes entities of type 'other' with <= 2 mentions inactive for > 30 days.
    Returns total number of updated/deleted entities.

    This runs post-every-digest (daily). The ``updated_at`` guard ensures a quiet
    entity is decayed at most ONCE PER WEEK rather than compounding 20% every day
    (the decay itself bumps ``updated_at``, as does any real mention via upsert).
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE entity_memory
               SET mention_count = GREATEST(1, ROUND(mention_count * 0.8)),
                   updated_at = now()
               WHERE last_mentioned < now()::date - INTERVAL '14 days'
                 AND updated_at    < now() - INTERVAL '7 days'
                 AND mention_count > 1"""
        )
        decayed = cur.rowcount

        cur.execute(
            """DELETE FROM entity_memory
               WHERE type = 'other'
                 AND last_mentioned < now()::date - INTERVAL '30 days'
                 AND mention_count <= 2"""
        )
        pruned = cur.rowcount
        return decayed + pruned



def seed_entities_from_protocols() -> int:
    """One-time seed of entity_memory from defillama_protocols. Skips existing slugs."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity_memory (slug, name, type, aliases, updated_at)
            SELECT
                slug,
                name,
                'protocol',
                ARRAY(SELECT DISTINCT lower(x) FROM unnest(
                    ARRAY[lower(name), lower(slug),
                          lower(COALESCE(token_symbol, ''))]
                ) AS x WHERE x <> ''),
                now()
            FROM defillama_protocols
            WHERE slug NOT IN (SELECT slug FROM entity_memory)
            RETURNING 1
            """
        )
        return len(cur.fetchall())


# --------------------------------------------------------------------------- #
# Analyst notes
# --------------------------------------------------------------------------- #
def insert_analyst_notes(d: "date_t", notes: str, entity_updates: dict,
                          source: str = "digest", model_used: str = "") -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
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
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT title, analysis FROM digest_bullet_analysis WHERE digest_date = %s",
            (digest_date,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}


# --------------------------------------------------------------------------- #
# Importance scoring (see app/scoring.py) — reference data + corroboration
# --------------------------------------------------------------------------- #
def _term_boundary_regex(terms: list[str]) -> str:
    """Build a POSIX word-boundary alternation regex (\\y…\\y) for ~* matching."""
    import re as _re
    parts = [_re.escape(t) for t in terms if t and len(t) >= 3]
    return r"\y(" + "|".join(parts) + r")\y" if parts else ""


def get_feed_items_matching_terms(terms: list[str], around_date: "date_t",
                                  window_hours: int = 24) -> list[dict]:
    """Return feed items whose content word-boundary-matches any of `terms`, within a
    `window_hours` window ending at the end of `around_date` (so it works for both the
    live digest and historical backfill). Used for corroboration + velocity signals."""
    pattern = _term_boundary_regex(terms)
    if not pattern:
        return []
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
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
        return cur.fetchall()


def get_protocol_tvls() -> list[tuple]:
    """Return (name, tvl_usd) for all protocols with a known TVL — for entity-weight scoring."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, tvl_usd FROM defillama_protocols"
            " WHERE name IS NOT NULL AND tvl_usd IS NOT NULL"
        )
        return cur.fetchall()


def get_entity_mention_map() -> list[tuple]:
    """Return (name, aliases, mention_count) for all entities — for entity-weight scoring."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT name, aliases, mention_count FROM entity_memory")
        return cur.fetchall()


def delete_bullet_analyses(digest_date: "date_t") -> int:
    """Delete all pre-computed analyses for a given digest date. Returns count deleted."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM digest_bullet_analysis WHERE digest_date = %s RETURNING 1",
            (digest_date,),
        )
        return len(cur.fetchall())


def prune_bullet_analyses(digest_date: "date_t", keep_titles: list[str]) -> int:
    """Delete rows for the date whose title is NOT in keep_titles — stale rows left by
    a superseded same-day digest run. No-op when keep_titles is empty (safety guard).
    Returns count deleted."""
    keep = [t for t in keep_titles if t]
    if not keep:
        return 0
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM digest_bullet_analysis WHERE digest_date = %s "
            "AND title <> ALL(%s::text[]) RETURNING 1",
            (digest_date, keep),
        )
        return len(cur.fetchall())


def get_entities_for_briefing(min_mentions: int = 3) -> list[tuple]:
    """Return (slug, name, type, aliases) for entities eligible for intel briefs."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT slug, name, type, aliases FROM entity_memory"
            " WHERE mention_count >= %s ORDER BY mention_count DESC",
            (min_mentions,),
        )
        return cur.fetchall()


def upsert_entity_intel_brief(entity_name: str, brief_html: str,
                               model_used: str, digest_date: "date_t") -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO entity_intel_brief (entity_name, brief_html, model_used, digest_date, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (entity_name) DO UPDATE SET
                brief_html  = EXCLUDED.brief_html,
                model_used  = EXCLUDED.model_used,
                digest_date = EXCLUDED.digest_date,
                updated_at  = now()
            """,
            (entity_name, brief_html, model_used, digest_date),
        )


def get_entity_intel_brief(query: str, max_age_days: int = 7) -> "dict | None":
    """Return a fresh intel brief for an entity matching query (case-insensitive name or alias).

    Checks entity_intel_brief by name first, then looks up canonical name via
    entity_memory aliases. Returns None if no brief exists or it is older than max_age_days.
    """
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT entity_name, brief_html, model_used, digest_date, updated_at
            FROM entity_intel_brief
            WHERE lower(entity_name) = lower(%s)
              AND digest_date >= CURRENT_DATE - %s::int
            LIMIT 1
            """,
            (query, max_age_days),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
        # Try alias match via entity_memory
        cur.execute(
            """
            SELECT em.name
            FROM entity_memory em
            WHERE lower(em.name) = lower(%s)
               OR lower(%s) = ANY(SELECT lower(a) FROM unnest(em.aliases) AS a)
            LIMIT 1
            """,
            (query, query),
        )
        em_row = cur.fetchone()
        if not em_row:
            return None
        cur.execute(
            """
            SELECT entity_name, brief_html, model_used, digest_date, updated_at
            FROM entity_intel_brief
            WHERE lower(entity_name) = lower(%s)
              AND digest_date >= CURRENT_DATE - %s::int
            LIMIT 1
            """,
            (em_row["name"], max_age_days),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_recent_analyst_notes(days: int = 7) -> list[tuple]:
    """Return (date, notes) rows for the last N days, newest first."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT date, notes FROM analyst_notes"
            " WHERE date >= CURRENT_DATE - %s::int"
            " ORDER BY date DESC, id DESC",
            (days,),
        )
        return cur.fetchall()


def get_recent_digests_text(days: int = 3) -> list[tuple]:
    """Return (date, content) for the last N successful digest days."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
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
        return cur.fetchall()


def get_digest_contents_for_dedup(days: int, before_date: "date_t | None" = None) -> list[tuple]:
    """Return (date, content) rows for dedup lookback.

    before_date given: digests from [before_date - days, before_date).
    before_date None:  digests from the last N days (excluding today).
    """
    with _conn() as conn, conn.cursor() as cur:
        if before_date:
            cur.execute(
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
        else:
            cur.execute(
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
        return cur.fetchall()


# --------------------------------------------------------------------------- #
# Weekly digest
# --------------------------------------------------------------------------- #
def insert_weekly_digest(week_start: "date_t", week_end: "date_t", content: str,
                          rotation: str = "MIXED", model_used: str = "",
                          trigger: str = "cron", duration_ms: int | None = None,
                          error: str | None = None) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
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
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT week_start, week_end, content, rotation, model_used,
                   to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
            FROM weekly_digest
            WHERE error IS NULL AND week_start <= %s AND week_end >= %s
            ORDER BY week_start DESC
            LIMIT 1
            """,
            (target_date, target_date),
        )
        return cur.fetchone()


def list_weekly_digests(limit: int = 12) -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
                   to_char(week_end,'YYYY-MM-DD')   AS week_end,
                   rotation,
                   to_char(created_at,'YYYY-MM-DD') AS created_at
            FROM weekly_digest WHERE error IS NULL
            ORDER BY week_start DESC LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def get_recent_weekly_digests(
    limit: int = 3, before_week_start: "date_t | None" = None
) -> list[dict]:
    """Return the N most recent successful weekly digests (optionally before a date).

    Used to inject previous-week context into the LLM prompt for continuity.
    """
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        if before_week_start is not None:
            cur.execute(
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
            )
        else:
            cur.execute(
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
            )
        return cur.fetchall()


def get_digests_for_range(start: "date_t", end: "date_t") -> list[tuple]:
    """Return (date, content) tuples for all digests within [start, end]."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (date) date, content
            FROM crypto_digest
            WHERE error IS NULL AND date >= %s AND date <= %s
            ORDER BY date DESC, created_at DESC
            """,
            (start, end),
        )
        return cur.fetchall()


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


def get_protocol_category_summary() -> list[dict]:
    """Aggregate TVL + avg 7d change by DeFiLlama category."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT category,
                   COUNT(*)           AS protocol_count,
                   SUM(tvl_usd)       AS total_tvl,
                   AVG(tvl_change_7d) AS avg_7d_change
            FROM defillama_protocols
            WHERE tvl_usd > 0 AND category IS NOT NULL AND category != ''
            GROUP BY category
            ORDER BY total_tvl DESC
            LIMIT 14
            """
        )
        return [
            {"category": r[0], "count": r[1], "tvl": r[2], "avg_7d_change": r[3]}
            for r in cur.fetchall()
        ]


def get_protocol_tvl_movers(limit: int = 12) -> list[dict]:
    """Top protocols by absolute 7d TVL change (gainers + losers), min $10M TVL."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT name, category, tvl_usd, tvl_change_7d, token_symbol
            FROM defillama_protocols
            WHERE tvl_usd > 10000000 AND tvl_change_7d IS NOT NULL
            ORDER BY ABS(tvl_change_7d) DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------- #
# Specialized agent — semantic search
# --------------------------------------------------------------------------- #
# Governance proposals (Snapshot)
# --------------------------------------------------------------------------- #

def upsert_governance_proposals(rows: list[dict]) -> int:
    """Upsert a list of proposal dicts; returns the number of rows processed."""
    if not rows:
        return 0
    sql = """
        INSERT INTO governance_proposals
            (proposal_id, space_id, space_name, title, state, start_ts, end_ts, fetched_at)
        VALUES (%(proposal_id)s, %(space_id)s, %(space_name)s, %(title)s,
                %(state)s, %(start_ts)s, %(end_ts)s, NOW())
        ON CONFLICT (proposal_id) DO UPDATE SET
            state      = EXCLUDED.state,
            end_ts     = EXCLUDED.end_ts,
            fetched_at = EXCLUDED.fetched_at
    """
    with _conn() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, row)
    return len(rows)


def get_active_governance_proposals(limit: int = 6) -> list[dict]:
    """Return active/pending proposals ordered by end time (soonest first)."""
    sql = """
        SELECT proposal_id, space_id, space_name, title, state, start_ts, end_ts
        FROM governance_proposals
        WHERE state = 'active'
        ORDER BY end_ts ASC NULLS LAST
        LIMIT %s
    """
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (limit,))
        return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
def search_feed(keyword: str, topk: int | None = None, days: int | None = None) -> list[dict]:
    topk = topk or config.SEARCH_TOPK
    days = days or config.SEARCH_WINDOW_DAYS
    qvec = _vec_literal(embeddings.embed(embeddings.clean_for_embedding(keyword) or keyword))
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET LOCAL ivfflat.probes = %s", (config.IVFFLAT_PROBES,))
        cur.execute(
            """
            SELECT content, link, creator, pub_date, source_type,
                   1 - (embedding <=> %s::vector) AS score
            FROM feed_items
            WHERE embedding IS NOT NULL
              AND COALESCE(pub_date, ingested_at) >= now() - make_interval(days => %s)
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (qvec, days, qvec, topk),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------- #
# Podcast episodes (YouTube transcripts + LLM analysis)
# --------------------------------------------------------------------------- #
def insert_podcast_episode(ep: dict) -> bool:
    """Insert a freshly-detected episode (status='pending'). Dedupe on video_id.

    Returns True if a new row was inserted, False if it already existed.
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO podcast_episodes (video_id, channel, channel_id, title, url, published_at)
            VALUES (%s, %s, %s, %s, %s, %s::timestamptz)
            ON CONFLICT (video_id) DO NOTHING
            """,
            (ep["video_id"], ep["channel"], ep.get("channel_id"),
             ep.get("title"), ep.get("url"), ep.get("published_at")),
        )
        return cur.rowcount > 0


def get_pending_podcast_episodes(limit: int = 8) -> list[dict]:
    """Episodes awaiting transcript+analysis, newest first.

    'pending' (never attempted) is always eligible. 'failed' episodes back off:
    only retried once their last attempt (fetched_at) is older than 24 h, so a
    permanently IP-blocked video isn't re-hit every cron run.
    """
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
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
        )
        return [dict(r) for r in cur.fetchall()]


def store_podcast_summary(video_id: str, transcript: str, transcript_lang: str | None,
                          duration_sec: int | None, summary: str, analysis: dict,
                          model_used: str, embedding: list[float] | None) -> None:
    """Persist transcript + map-reduce analysis; flip status to 'summarized'."""
    vec = _vec_literal(embedding) if embedding else None
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
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
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE podcast_episodes SET status = %s, error = %s, fetched_at = now() WHERE video_id = %s",
            (status, error, video_id),
        )


def get_recent_podcast_summaries(hours: int = 48) -> list[dict]:
    """Summarized episodes published within the window, newest first — for digest context."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT video_id, channel, title, url, published_at, summary, analysis
            FROM podcast_episodes
            WHERE status = 'summarized'
              AND COALESCE(published_at, created_at) >= now() - make_interval(hours => %s)
            ORDER BY published_at DESC NULLS LAST
            """,
            (hours,),
        )
        return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Narratives (see app/narratives.py) — gather sources + full-rebuild persist
# --------------------------------------------------------------------------- #
def get_bullet_analyses_window(days: int = 14) -> list[dict]:
    """News signals: per-bullet rows from the last N digest days (newest first)."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, digest_date, title, body, analysis, importance_score, source_count
            FROM digest_bullet_analysis
            WHERE digest_date >= CURRENT_DATE - %s::int
            ORDER BY digest_date DESC, importance_score DESC NULLS LAST
            """,
            (days,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_podcast_summaries_window(days: int = 14) -> list[dict]:
    """Podcast signals: summarized episodes within the window, newest first."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT video_id, channel, title, url, published_at, summary, analysis
            FROM podcast_episodes
            WHERE status = 'summarized' AND analysis IS NOT NULL
              AND COALESCE(published_at, created_at) >= now() - make_interval(days => %s)
            ORDER BY published_at DESC NULLS LAST
            """,
            (days,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_governance_signals_window(days: int = 21) -> list[dict]:
    """Governance signals: proposals seen within the window, newest first."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT proposal_id, space_id, space_name, title, state, start_ts, end_ts
            FROM governance_proposals
            WHERE COALESCE(start_ts, fetched_at) >= now() - make_interval(days => %s)
            ORDER BY start_ts DESC NULLS LAST
            """,
            (days,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_entities_for_matching(min_mentions: int = 2) -> list[dict]:
    """(slug, name, aliases, type, mention_count) for entity resolution in clustering."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT slug, name, aliases, type, mention_count FROM entity_memory "
            "WHERE mention_count >= %s AND type <> 'other'",
            (min_mentions,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_existing_narratives() -> list[dict]:
    """Existing narratives for label/thesis reuse during a rebuild."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT slug, label, thesis, watch_next, contrarian, entity_slugs, "
            "signal_count, model_used FROM narratives"
        )
        return [dict(r) for r in cur.fetchall()]


def replace_narratives(narratives: list[dict], signals: dict) -> int:
    """Full-rebuild: wipe narratives (cascade clears narrative_signals) + insert the new set.

    narratives: [{slug, label, thesis, entity_slugs, centroid(list|None), state,
                  intensity_48h, baseline, momentum_ratio, delta_48h, signal_count,
                  dominant_type, severity, first_seen, last_signal_at, model_used}]
    signals: {slug: [{signal_type, signal_ref, title, body, url, importance, ts}]}
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM narratives")
        for n in narratives:
            centroid = n.get("centroid")
            cur.execute(
                """
                INSERT INTO narratives
                    (slug, label, thesis, watch_next, contrarian, entity_slugs, centroid,
                     state, intensity_48h, baseline, momentum_ratio, delta_48h, signal_count,
                     dominant_type, severity, first_seen, last_signal_at, model_used, updated_at)
                VALUES (%s, %s, %s, %s::text[], %s, %s::text[], %s::vector, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (n["slug"], n["label"], n.get("thesis"), n.get("watch_next") or [],
                 n.get("contrarian"), n.get("entity_slugs") or [],
                 _vec_literal(centroid) if centroid else None,
                 n.get("state", "forming"), n.get("intensity_48h"), n.get("baseline"),
                 n.get("momentum_ratio"), n.get("delta_48h", 0), n.get("signal_count", 0),
                 n.get("dominant_type"), n.get("severity"), n.get("first_seen"),
                 n.get("last_signal_at"), n.get("model_used", "")),
            )
            rows = signals.get(n["slug"]) or []
            if rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO narrative_signals
                        (narrative_slug, signal_type, signal_ref, title, body, url, importance, ts)
                    VALUES %s ON CONFLICT DO NOTHING
                    """,
                    [(n["slug"], s["signal_type"], s["signal_ref"], s.get("title"),
                      s.get("body"), s.get("url"), s.get("importance"), s.get("ts"))
                     for s in rows],
                    template="(%s, %s, %s, %s, %s, %s, %s, %s::timestamptz)",
                )
    return len(narratives)
