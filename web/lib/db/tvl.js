// app/lib/db/tvl.js — DeFiLlama TVL snapshots + day-over-day change + protocol league
import { unstable_cache } from "next/cache";
import { safeRows } from "./_core.js";

// Curated brand → DeFiLlama product slugs. The auto-matcher in getEntityLeague resolves a
// DeFiLlama protocol to its brand entity by slug (exact → version-base `-v\d+$` → first slug
// token + name prefix). That misses brands whose entity slug differs from DeFiLlama's product
// slug-token: `ether-fi` vs `ether.fi-stake` (dot↔dash), `usde`/`skyecosystem` vs `sky-lending`
// (different root word), `eigenlayer` vs `eigencloud` (renamed product). A generic alias-prefix
// match over-aggregates (e.g. `ether`→ethena, `frax-usd`→every frax product, `hypestrat`→
// hyperliquid), so these high-value brands are mapped EXPLICITLY — each DeFiLlama slug listed
// here rolls its TVL up into the named entity. Keys are entity slugs; values are exact
// DeFiLlama `defillama_protocols.slug` values. Add only unambiguous, same-brand products.
const BRAND_DEFILLAMA_ALIASES = {
  "ether-fi": ["ether.fi-stake", "ether.fi-liquid", "etherfi-borrowing-market", "etherfi-cash-liquid"],
  "ondo-finance": ["ondo-yield-assets", "ondo-global-markets"],
  "eigenlayer": ["eigencloud"],
  "curve-finance": ["curve-dex", "curve-llamalend", "crvusd"],
  "kamino-finance": ["kamino-lend", "kamino-liquidity"],
  "skyecosystem": ["sky-lending", "sky-money", "sky-rwa"],
  "spark-fi": ["sparklend", "spark-liquidity-layer", "spark-savings"],
  "lista-dao": ["lista-lending", "lista-liquid-staking", "lista-cdp", "lista-dex"],
  "aerodrome-finance": ["aerodrome-slipstream", "aerodrome-v1", "aerodrome-ignition"],
  "velodrome-finance": ["velodrome-v3", "velodrome-v2"],
  "venus-protocol": ["venus-core-pool"],
  "centrifuge": ["centrifuge-protocol"],
  "eulerfinance": ["euler-v2"],
  "jito-sol": ["jito-liquid-staking", "jito-restaking"],
  "pump-fun": ["pumpswap"],
  "chainlink": ["ccip"],
};

// Render BRAND_DEFILLAMA_ALIASES as a SQL `VALUES (dp_slug, entity_slug), …` body. Values are
// hardcoded constants (no user input) — safe to inline; slugs are [a-z0-9.-] only.
function brandAliasValues() {
  const pairs = [];
  for (const [entity, dpSlugs] of Object.entries(BRAND_DEFILLAMA_ALIASES)) {
    for (const dp of dpSlugs) pairs.push(`('${dp}','${entity}')`);
  }
  return pairs.join(",");
}


// Directory of every tracked chain (from entity_memory, our own DB), each with its latest
// defillama_tvl reading where we snapshot it (only ~6 chains) — else null. Used by
// buildProjectHints to attach chain chips + logos to daily bullets WITHOUT a live
// api.llama.fi call: the chip logo is the deterministic icons.llamao.fi URL built from the
// chain name, so we only need the name universe (broad) + optional TVL. Cached 1h — the
// chain roster barely moves. Ordered TVL-first then by coverage so the ranking mirrors the
// old TVL-sorted llama.fi list for the tracked chains.
export const getChainDirectory = unstable_cache(
  async () =>
    safeRows(
      `SELECT e.name,
              e.logo_url,
              t.tvl_usd::float8 AS tvl_usd
       FROM entity_memory e
       LEFT JOIN LATERAL (
         SELECT tvl_usd
         FROM defillama_tvl
         WHERE lower(chain) = lower(e.name) AND chain <> 'total'
         ORDER BY date DESC
         LIMIT 1
       ) t ON true
       WHERE e.type = 'chain' AND e.mention_count >= 2
       ORDER BY t.tvl_usd DESC NULLS LAST, e.mention_count DESC`
    ),
  ["horyon-chain-directory"],
  { revalidate: 3600 }
);


// Latest TVL snapshot per chain from defillama_tvl, sorted total-first then tvl desc.
export async function getTvlSnapshot() {
  const rows = await safeRows(
    `SELECT DISTINCT ON (chain)
        to_char(date,'YYYY-MM-DD') AS date,
        chain,
        tvl_usd::float8 AS tvl_usd
     FROM defillama_tvl
     ORDER BY chain, date DESC`
  );
  return rows.sort((a, b) => {
    if (a.chain === "total") return -1;
    if (b.chain === "total") return 1;
    return b.tvl_usd - a.tvl_usd;
  });
}


// Latest two TVL readings per chain; computes day-over-day % change.
// Cached 5 min — TVL snapshot is written by the bot ~twice daily.
export const getTvlWithChange = unstable_cache(
  async () => {
    const rows = await safeRows(
      `WITH ranked AS (
         SELECT chain,
                date,
                tvl_usd::float8 AS tvl_usd,
                ROW_NUMBER() OVER (PARTITION BY chain ORDER BY date DESC) AS rn
         FROM defillama_tvl
       )
       SELECT
         chain,
         to_char(MAX(date) FILTER (WHERE rn = 1), 'YYYY-MM-DD') AS date,
         MAX(tvl_usd)       FILTER (WHERE rn = 1)                AS tvl_now,
         MAX(tvl_usd)       FILTER (WHERE rn = 2)                AS tvl_prev
       FROM ranked
       WHERE rn <= 2
       GROUP BY chain`
    );
    return rows
      .map((r) => ({
        chain:   r.chain,
        date:    r.date,
        tvl_now: parseFloat(r.tvl_now),
        tvl_prev: r.tvl_prev ? parseFloat(r.tvl_prev) : null,
        pct: r.tvl_prev
          ? ((parseFloat(r.tvl_now) - parseFloat(r.tvl_prev)) / parseFloat(r.tvl_prev)) * 100
          : null,
      }))
      .sort((a, b) => {
        if (a.chain === "total") return -1;
        if (b.chain === "total") return 1;
        return b.tvl_now - a.tvl_now;
      });
  },
  ["horyon-tvl-change"],
  { revalidate: 300 }
);


// ── Entity league — the Atlas "Index" screener (ALL entities) ────────────────
// A ranked, sortable table of EVERY tracked entity (protocols, chains, exchanges,
// funds, people, …), not just protocols. The spine is `entity_memory`, so coverage
// (`mention_count`) and centrality (`degree` from `entity_edges`) are universal and
// exact for every type. DeFiLlama TVL/flows are layered on where they exist.
//
// BRAND-LEVEL TVL (the subtle bit): DeFiLlama splits a brand into versioned/product
// slugs (`aave-v3`, `uniswap-v4`, `morpho-blue`), so a direct `defillama.slug =
// entity.slug` join leaves the BRAND entity (`aave`, `uniswap`) with no TVL at all.
// We instead resolve every live protocol → its canonical entity (exact slug → version
// base `-v\d+$` → brand root, branches 2/3 TYPE-GATED to protocol/dao + a name-prefix
// check so an exchange/chain root never leaks — `binance-staked-eth`→`binance`/exchange
// and `base-bridge`→`base`/chain are rejected), then AGGREGATE the protocols up to the
// entity: tvl = Σ tvl, 7d/1d = TVL-weighted mean, category/token/chains from the largest.
// Result: `aave` shows its full ~$12B brand TVL alongside its real 343-mention coverage.
export async function getEntityLeague({ limit = 200 } = {}) {
  const rows = await safeRows(
    `WITH ew AS (
       SELECT slug_a, slug_b, weight FROM entity_edges WHERE weight >= 2
     ),
     deg AS (
       SELECT slug, sum(weight)::int AS strength, count(*)::int AS degree
       FROM (SELECT slug_a AS slug, weight FROM ew
             UNION ALL SELECT slug_b, weight FROM ew) u
       GROUP BY slug
     ),
     live AS (
       SELECT slug, name, tvl_usd, tvl_change_1d, tvl_change_7d, category, chains,
              token_symbol, logo_url
       FROM defillama_protocols
       WHERE tvl_usd > 0 AND fetched_at > now() - interval '3 days'
     ),
     brand_alias (dp_slug, entity_slug) AS (
       VALUES ${brandAliasValues()}
     ),
     prot_map AS (
       -- Curated brand alias wins over the slug heuristic, which wins over the raw slug.
       SELECT l.*, COALESCE(ba.entity_slug, r.slug, l.slug) AS entity_slug
       FROM live l
       LEFT JOIN brand_alias ba ON ba.dp_slug = l.slug
       LEFT JOIN LATERAL (
         SELECT em.slug FROM entity_memory em
         WHERE em.slug = l.slug
            OR (em.type IN ('protocol','dao') AND em.slug <> l.slug
                AND em.slug = regexp_replace(l.slug, '-v[0-9]+$', ''))
            OR (em.type IN ('protocol','dao') AND em.slug <> l.slug
                AND em.slug = split_part(l.slug, '-', 1)
                AND l.name ILIKE em.name || '%')
         ORDER BY em.mention_count DESC NULLS LAST, (em.slug = l.slug) DESC
         LIMIT 1
       ) r ON true
     ),
     -- Numeric roll-up (sum tvl, TVL-weighted flow). Kept separate from the array/text
     -- attributes below: array_agg() over the text[] chains column throws "cannot
     -- accumulate arrays of different dimensionality" when an entity mixes an empty
     -- {} chains array with a populated one — so the largest protocol's chains/
     -- category/token/logo come from a DISTINCT ON instead.
     fund_sum AS (
       SELECT entity_slug,
              sum(tvl_usd)::float8 AS tvl_usd,
              (sum(tvl_usd * tvl_change_7d) FILTER (WHERE tvl_change_7d IS NOT NULL)
                 / NULLIF(sum(tvl_usd) FILTER (WHERE tvl_change_7d IS NOT NULL), 0))::float8 AS tvl_change_7d,
              (sum(tvl_usd * tvl_change_1d) FILTER (WHERE tvl_change_1d IS NOT NULL)
                 / NULLIF(sum(tvl_usd) FILTER (WHERE tvl_change_1d IS NOT NULL), 0))::float8 AS tvl_change_1d,
              count(*)::int AS protocol_count
       FROM prot_map GROUP BY entity_slug
     ),
     fund_top AS (
       SELECT DISTINCT ON (entity_slug)
              entity_slug, category, token_symbol, chains, logo_url
       FROM prot_map ORDER BY entity_slug, tvl_usd DESC NULLS LAST
     ),
     -- Chain-level TVL: protocols have no TVL for an L1/L2 entity (ethereum, base, …),
     -- so layer in the per-chain DeFiLlama snapshot (defillama_tvl, chain NAME ~ entity
     -- slug). Only a handful of chains are tracked today — that's all the data we have.
     -- 7d change from the snapshot ~7 days back (daily history, so it exists).
     chain_tvl AS (
       SELECT n.slug, n.tvl_now,
              CASE WHEN w.tvl_wk > 0 THEN ((n.tvl_now - w.tvl_wk) / w.tvl_wk * 100)::float8 END AS d7
       FROM (
         SELECT DISTINCT ON (chain) lower(chain) AS slug, tvl_usd::float8 AS tvl_now
         FROM defillama_tvl WHERE chain <> 'total' ORDER BY chain, date DESC
       ) n
       LEFT JOIN (
         SELECT DISTINCT ON (chain) lower(chain) AS slug, tvl_usd::float8 AS tvl_wk
         FROM defillama_tvl WHERE chain <> 'total' AND date <= CURRENT_DATE - 7
         ORDER BY chain, date DESC
       ) w ON w.slug = n.slug
     )
     SELECT e.slug, e.name, e.type, e.mention_count, e.digest_mention_count,
            e.last_mentioned, e.twitter_handle,
            COALESCE(ft.logo_url, e.logo_url) AS logo_url,
            COALESCE(fs.tvl_usd, ct.tvl_now)        AS tvl_usd,
            COALESCE(fs.tvl_change_7d, ct.d7)       AS tvl_change_7d,
            fs.tvl_change_1d, fs.protocol_count,
            ft.category, ft.token_symbol, ft.chains,
            d.degree, d.strength,
            n.state AS narrative_state, n.label AS narrative_label, n.slug AS narrative_slug,
            (av.slug IS NOT NULL) AS avatar_cached,
            conn.connections
       FROM entity_memory e
       LEFT JOIN fund_sum fs       ON fs.entity_slug = e.slug
       LEFT JOIN fund_top ft       ON ft.entity_slug = e.slug
       LEFT JOIN chain_tvl ct      ON ct.slug = e.slug
       LEFT JOIN deg d             ON d.slug = e.slug
       LEFT JOIN entity_avatars av ON av.slug = e.slug
       LEFT JOIN LATERAL (
         SELECT state, label, slug FROM narratives
         WHERE e.slug = ANY(entity_slugs)
         ORDER BY array_position(
           ARRAY['heating','forming','steady','cooling','dormant']::text[], state)
         LIMIT 1
       ) n ON true
       -- Top co-mentioned neighbours (for the Connections avatar stack), with each
       -- neighbour's avatar fields resolved (logo → twitter → cached flag).
       LEFT JOIN LATERAL (
         SELECT json_agg(json_build_object(
                  'slug', ne.slug, 'name', ne.name, 'type', ne.type,
                  'logo_url', COALESCE(np.logo_url, ne.logo_url),
                  'twitter_handle', ne.twitter_handle,
                  'avatar_cached', (nav.slug IS NOT NULL)
                ) ORDER BY x.weight DESC) AS connections
         FROM (
           SELECT CASE WHEN slug_a = e.slug THEN slug_b ELSE slug_a END AS slug, weight
           FROM entity_edges
           WHERE (slug_a = e.slug OR slug_b = e.slug) AND weight >= 2
           ORDER BY weight DESC LIMIT 5
         ) x
         JOIN entity_memory ne          ON ne.slug = x.slug
         LEFT JOIN defillama_protocols np ON np.slug = ne.slug
         LEFT JOIN entity_avatars nav   ON nav.slug = ne.slug
       ) conn ON true
      WHERE e.mention_count >= 2
      ORDER BY e.mention_count DESC
      LIMIT $1`,
    [limit]
  );
  return rows.map((r) => ({
    slug: r.slug,
    entitySlug: r.slug,           // entity IS the spine — neighbor lookups key on slug
    name: r.name,
    // DAOs fold into Protocols on the map (same as getEntityGraph) — keeps the type
    // filter's 6 buckets consistent across all three views.
    type: r.type === "dao" ? "protocol" : (r.type || "other"),
    category: r.category || null,
    tvl: r.tvl_usd ?? null,
    tvlChange1d: r.tvl_change_1d ?? null,
    tvlChange7d: r.tvl_change_7d ?? null,
    mcapTvl: null,                // unavailable from DeFiLlama's list endpoint (see panel)
    tokenSymbol: r.token_symbol || null,
    chains: Array.isArray(r.chains) ? r.chains : [],
    protocolCount: r.protocol_count ?? 0,
    logoUrl: r.logo_url || null,
    avatarCached: r.avatar_cached === true,
    mentionCount: r.mention_count ?? 0,
    // "Horyon coverage": distinct daily-brief bullets that cited this entity — the
    // curated-output metric, vs mentionCount's raw cross-source mentions.
    digestMentionCount: r.digest_mention_count ?? 0,
    lastMentioned: r.last_mentioned ? r.last_mentioned.toISOString().slice(0, 10) : null,
    twitterHandle: r.twitter_handle || null,
    degree: r.degree ?? 0,
    strength: r.strength ?? 0,
    // Top co-mentioned neighbours (avatar stack in the Connections column).
    connections: Array.isArray(r.connections)
      ? r.connections.map((c) => ({
          slug: c.slug,
          name: c.name,
          type: c.type === "dao" ? "protocol" : (c.type || "other"),
          logoUrl: c.logo_url || null,
          twitterHandle: c.twitter_handle || null,
          avatarCached: c.avatar_cached === true,
        }))
      : [],
    narrativeState: r.narrative_state || null,
    narrativeLabel: r.narrative_label || null,
    narrativeSlug: r.narrative_slug || null,
  }));
}
