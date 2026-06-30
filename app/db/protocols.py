"""app.db.protocols — DeFiLlama TVL + protocol metadata."""
from __future__ import annotations

import json
from datetime import date as date_t

from psycopg2.extras import execute_values

from ._core import _conn, _fetchall


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
    rows = _fetchall(
        """
        SELECT DISTINCT ON (chain) date, chain, tvl_usd
        FROM defillama_tvl
        ORDER BY chain, date DESC
        """
    )
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
    return _fetchall(
        """SELECT name, category, chains, tvl_usd,
                  tvl_change_1d, tvl_change_7d, url, token_symbol, gecko_id
           FROM defillama_protocols
           ORDER BY tvl_usd DESC
           LIMIT %s""",
        (limit,),
    )


def get_protocols_by_slugs(slugs: list[str]) -> list[dict]:
    """Return TVL + metadata from defillama_protocols for the given slugs."""
    if not slugs:
        return []
    return _fetchall(
        """SELECT slug, name, category, tvl_usd, tvl_change_1d, tvl_change_7d
           FROM defillama_protocols WHERE slug = ANY(%s)""",
        (slugs,),
        dict_rows=True,
    )


def get_protocol_tvls() -> list[tuple]:
    """Return (name, tvl_usd) for all protocols with a known TVL — for entity-weight scoring."""
    return _fetchall(
        "SELECT name, tvl_usd FROM defillama_protocols"
        " WHERE name IS NOT NULL AND tvl_usd IS NOT NULL"
    )


def get_protocol_category_summary() -> list[dict]:
    """Aggregate TVL + avg 7d change by DeFiLlama category."""
    return [
        {"category": r[0], "count": r[1], "tvl": r[2], "avg_7d_change": r[3]}
        for r in _fetchall(
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
    ]


def get_protocol_tvl_movers(limit: int = 12) -> list[dict]:
    """Top protocols by absolute 7d TVL change (gainers + losers), min $10M TVL."""
    return _fetchall(
        """
        SELECT name, category, tvl_usd, tvl_change_7d, token_symbol
        FROM defillama_protocols
        WHERE tvl_usd > 10000000 AND tvl_change_7d IS NOT NULL
        ORDER BY ABS(tvl_change_7d) DESC
        LIMIT %s
        """,
        (limit,),
        dict_rows=True,
    )
