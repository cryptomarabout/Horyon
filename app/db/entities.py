"""app.db.entities — entity_memory (canonical entities, aliases, types) + intel briefs."""
from __future__ import annotations

import re
from datetime import date as date_t

from psycopg2.extras import RealDictCursor

from ._core import _conn, _execute, _fetchall, _fetchone


# --------------------------------------------------------------------------- #
# Entity memory
# --------------------------------------------------------------------------- #
_ERC_RE = __import__("re").compile(
    r"^(ERC|EIP|BIP|CIP|AIP|SIP|RIP)-\d+$", __import__("re").IGNORECASE
)


# ── Matcher-cache invalidation ────────────────────────────────────────────────
# app.entities caches its compiled alias matcher process-wide (a full entity_memory
# read + regex recompile) and rebuilds it only when this token changes. INVARIANT:
# every function in this module that writes entity_memory bumps the generation, so a
# cached matcher can never be stale. Cheap monotonic int; single bot process.
_ENTITY_GENERATION = 0


def _bump_entity_generation() -> None:
    global _ENTITY_GENERATION
    _ENTITY_GENERATION += 1


def entity_generation() -> int:
    """Monotonic token that increments on every entity_memory write.

    app.entities keys its cached matcher on this so detect_entities_* doesn't
    re-query entity_memory on every call (dozens per digest run)."""
    return _ENTITY_GENERATION


# Curated entity-type corrections. The LLM extractor (and CoinGecko / feed seeds)
# repeatedly misclassify a handful of well-known entities — e.g. tagging a Layer-1
# blockchain as a "protocol", or a regulated exchange as a "dao". Because upsert
# OVERWRITES `type` on every re-extraction, a one-off DB edit gets reverted next
# ingest; enforcing the correction HERE — the single chokepoint every upsert path
# (extraction, coingecko seed, feed seed) flows through — makes it durable. Keep
# this list tight + human-verified; slug → canonical type.
CANONICAL_ENTITY_TYPES: dict[str, str] = {
    # Layer-1 blockchains with their own chain + native coin, not DeFi protocols.
    "bitcoin":  "chain",
    "zcash":    "chain",
    "monero":   "chain",
    "dogecoin": "chain",
    # Regulated prediction-market exchange — not a DAO.
    "kalshi":   "exchange",
}


# ── Entity review verdicts (decision memory) ─────────────────────────────────
# The audit's review-only classes (collision-risk, generically-named junk) used to
# re-flag the same entities on every run, and a junk entity deleted by hand was
# re-created by the next LLM extraction cycle within days. entity_review stores the
# human/purge verdict durably: 'block' → upsert_entity refuses to (re)create the slug;
# 'keep' → the audit stops flagging it. Loaded per-call sets are tiny (dozens of rows).

def get_entity_reviews() -> dict[str, str]:
    """slug → verdict ('keep' | 'block'). Empty dict if the table doesn't exist yet."""
    try:
        return {slug: verdict for slug, verdict in _fetchall(
            "SELECT slug, verdict FROM entity_review")}
    except Exception:
        return {}


def set_entity_review(slug: str, verdict: str, reason: str = "") -> None:
    """Record a durable keep/block verdict for a slug."""
    if verdict not in ("keep", "block"):
        raise ValueError(f"invalid verdict {verdict!r}")
    _execute(
        """INSERT INTO entity_review (slug, verdict, reason, decided_at)
           VALUES (%s, %s, %s, now())
           ON CONFLICT (slug) DO UPDATE SET
               verdict = EXCLUDED.verdict, reason = EXCLUDED.reason, decided_at = now()""",
        (slug, verdict, reason),
    )


def _is_blocked(slug: str) -> bool:
    try:
        row = _fetchall(
            "SELECT 1 FROM entity_review WHERE slug = %s AND verdict = 'block'", (slug,))
        return bool(row)
    except Exception:
        return False  # table missing on a fresh volume — never break ingest


def prose_doc_count(term: str, cap: int = 60) -> "int | None":
    """How many feed_items documents contain `term` as a word, capped at `cap`.

    Returns None when the term is an English stopword ("would", "just", …) —
    to_tsquery drops it, which is itself the strongest possible evidence the
    term is ordinary prose. Uses the content_tsv GIN index; the LIMIT means a
    very common word never triggers a full scan. Used by extraction to refuse
    minting a NEW entity whose only identity is a common prose word."""
    t = (term or "").strip()
    if not t or not t.isalpha():
        return 0
    try:
        row = _fetchone(
            "SELECT count(*) FROM ("
            "  SELECT 1 FROM feed_items"
            "  WHERE content_tsv @@ plainto_tsquery('english', %s) LIMIT %s"
            ") q",
            (t, cap),
        )
        n = row[0] if row else 0
        if n == 0:
            # distinguish "rare term" from "stopword dropped by the parser"
            empty = _fetchone(
                "SELECT numnode(plainto_tsquery('english', %s)) = 0", (t,))
            if empty and empty[0]:
                return None
        return int(n)
    except Exception:
        return 0  # fail open: never block ingest on a probe error


def upsert_entity(slug: str, name: str, type_: str, aliases: list[str],
                  last_mentioned: "date_t | None" = None,
                  twitter_handle: str | None = None) -> None:
    """Insert or merge an entity. Aliases are union-merged; summary never overwritten.
    twitter_handle and logo_url are only updated when a non-None value is provided.
    """
    # Token/governance standards are specifications, not entities.
    if _ERC_RE.match(name):
        return
    # Durably-blocked junk (entity_review verdict='block') must never be re-created
    # by the next extraction cycle — the audit → delete → re-extract loop this kills.
    if _is_blocked(slug):
        return
    # Pin curated entities to their human-verified type (survives re-extraction).
    type_ = CANONICAL_ENTITY_TYPES.get(slug, type_)
    _execute(
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
    _bump_entity_generation()


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
    if _is_blocked(slug):
        return
    _execute(
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
    _bump_entity_generation()


def update_entity_summary(slug: str, summary: str) -> None:
    _execute(
        "UPDATE entity_memory SET summary = %s, updated_at = now() WHERE slug = %s",
        (summary, slug),
    )


def get_all_entity_aliases() -> list[tuple]:
    """Return (slug, name, type, aliases, summary, mention_count) per entity, hottest first."""
    return _fetchall(
        "SELECT slug, name, type, aliases, summary, mention_count"
        " FROM entity_memory ORDER BY mention_count DESC"
    )


def get_entities_by_slugs(slugs: list[str]) -> list[dict]:
    if not slugs:
        return []
    return _fetchall(
        "SELECT slug, name, type, summary, twitter_handle, aliases FROM entity_memory WHERE slug = ANY(%s)",
        (slugs,),
        dict_rows=True,
    )


def get_governance_for_entity(slug: str, name: str, limit: int = 2) -> list[dict]:
    """Recent Snapshot governance proposals for an entity, newest-ending first.

    Matched by Snapshot space (``space_id`` like 'aave.eth' starts with the slug) or by
    a WORD-BOUNDARY match on ``space_name`` — never a raw substring, since this block is
    labelled "VERIFIED DATABASE FACTS" in bullet analysis and a piggyback substring match
    (e.g. 'arb' inside 'arbitrum') would launder a wrong governance fact as authoritative.
    Returns [{title, state}] — best-effort context (callers wrap in try/except)."""
    if not slug and not name:
        return []
    clauses, params = [], []
    if slug:
        clauses.append("space_id ILIKE %s")
        params.append(f"{slug}.%")
    if name:
        clauses.append("space_name ~* %s")
        params.append(r"\y" + re.escape(name) + r"\y")
    if not clauses:
        return []
    params.append(limit)
    return _fetchall(
        f"""SELECT title, state FROM governance_proposals
            WHERE {' OR '.join(clauses)}
            ORDER BY end_ts DESC NULLS LAST
            LIMIT %s""",
        params,
        dict_rows=True,
    )


def touch_entity_mentions(slugs: list[str], mention_date: "date_t") -> None:
    if not slugs:
        return
    _execute(
        """UPDATE entity_memory
           SET last_mentioned = GREATEST(last_mentioned, %s),
               mention_count  = mention_count + 1,
               updated_at     = now()
           WHERE slug = ANY(%s)""",
        (mention_date, slugs),
    )
    _bump_entity_generation()


def update_digest_mention_counts(counts: "dict[str, int]") -> int:
    """Set entity_memory.digest_mention_count from {slug: distinct-bullet count} — the
    "Horyon coverage" metric (mentions in curated daily briefs, distinct from raw
    source mention_count). Zeroes every entity absent from `counts` so a slug that
    dropped out of recent briefs doesn't keep a stale count. Returns the number of
    entities given a non-zero count."""
    from psycopg2.extras import execute_values
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE entity_memory SET digest_mention_count = 0 "
            "WHERE digest_mention_count <> 0"
        )
        items = [(slug, n) for slug, n in counts.items() if n]
        if items:
            execute_values(
                cur,
                "UPDATE entity_memory AS e SET digest_mention_count = v.n "
                "FROM (VALUES %s) AS v(slug, n) WHERE e.slug = v.slug",
                items,
            )
    _bump_entity_generation()
    return len(items)


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
    _bump_entity_generation()
    return decayed + pruned


def seed_entities_from_protocols() -> int:
    """One-time seed of entity_memory from defillama_protocols. Skips existing slugs."""
    n = _execute(
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
        """
    )
    _bump_entity_generation()
    return n


def get_entity_mention_map() -> list[tuple]:
    """Return (name, aliases, type, mention_count) for all entities — for entity-weight
    scoring (type lets scoring apply the shared ``matchable_term`` gate, which needs it
    to keep short distinctive tickers like Sui/GHO while rejecting junk aliases)."""
    return _fetchall("SELECT name, aliases, type, mention_count FROM entity_memory")


def get_entities_for_briefing(min_mentions: int = 3) -> list[tuple]:
    """Return (slug, name, type, aliases) for entities eligible for intel briefs."""
    return _fetchall(
        "SELECT slug, name, type, aliases FROM entity_memory"
        " WHERE mention_count >= %s ORDER BY mention_count DESC",
        (min_mentions,),
    )


def upsert_entity_intel_brief(entity_name: str, brief_html: str,
                               model_used: str, digest_date: "date_t") -> None:
    _execute(
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


def get_entities_for_matching(min_mentions: int = 2) -> list[dict]:
    """(slug, name, aliases, type, mention_count) for entity resolution in clustering."""
    return [dict(r) for r in _fetchall(
        "SELECT slug, name, aliases, type, mention_count FROM entity_memory "
        "WHERE mention_count >= %s AND type <> 'other' ORDER BY mention_count DESC",
        (min_mentions,),
        dict_rows=True,
    )]
