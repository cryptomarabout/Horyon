import { Pool } from "pg";

// Reuse one pool across hot-reloads / requests.
const g = globalThis;
export const pool =
  g._cryptoPool ??
  new Pool({ connectionString: process.env.DATABASE_URL, max: 4 });
if (!g._cryptoPool) g._cryptoPool = pool;

// One entry per day (latest digest for that date), newest first.
export async function listDigests() {
  const { rows } = await pool.query(
    `SELECT DISTINCT ON (date)
        to_char(date,'YYYY-MM-DD') AS date,
        to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at,
        (length(content) - length(replace(content,'•',''))) AS bullets
     FROM crypto_digest
     ORDER BY date DESC, created_at DESC`
  );
  return rows;
}

export async function getDigest(date) {
  const { rows } = await pool.query(
    `SELECT to_char(date,'YYYY-MM-DD') AS date,
            to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at,
            content
     FROM crypto_digest
     WHERE date = $1::date
     ORDER BY created_at DESC
     LIMIT 1`,
    [date]
  );
  return rows[0] ?? null;
}

export async function latestDate() {
  // Skip errored / empty digest rows so the home never lands on a failed run.
  const { rows } = await pool.query(
    `SELECT to_char(max(date),'YYYY-MM-DD') AS date FROM crypto_digest
     WHERE error IS NULL AND content IS NOT NULL AND content <> ''`
  );
  return rows[0]?.date ?? null;
}

// Latest TVL snapshot per chain from defillama_tvl, sorted total-first then tvl desc.
export async function getTvlSnapshot() {
  try {
    const { rows } = await pool.query(
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
  } catch {
    return [];
  }
}

// Active + pending Snapshot governance proposals, ordered soonest-ending first.
export async function getGovernanceProposals(limit = 6) {
  try {
    const { rows } = await pool.query(
      `SELECT proposal_id, space_id, space_name, title, state,
              start_ts, end_ts
       FROM governance_proposals
       WHERE state = 'active'
       ORDER BY end_ts ASC NULLS LAST
       LIMIT $1`,
      [limit]
    );
    return rows.map(r => ({
      ...r,
      end_ts: r.end_ts ? r.end_ts.toISOString() : null,
    }));
  } catch {
    return [];
  }
}

// Recent summarized podcast episodes for the Podcasts sidebar section + panel.
// `analysis` is JSONB → already a parsed object via node-postgres.
export async function getRecentPodcasts(limit = 40) {
  try {
    const { rows } = await pool.query(
      `SELECT video_id, channel, title, url, published_at, analysis
       FROM podcast_episodes
       WHERE status = 'summarized' AND analysis IS NOT NULL
       ORDER BY published_at DESC NULLS LAST
       LIMIT $1`,
      [limit]
    );
    return rows.map(r => ({
      ...r,
      published_at: r.published_at ? r.published_at.toISOString() : null,
    }));
  } catch {
    return [];
  }
}

// Summarized podcast episodes published on a specific UTC date — for inline
// "podcasts as daily news" rows in the daily feed. Same shape as getRecentPodcasts.
export async function getPodcastsForDate(date) {
  try {
    const { rows } = await pool.query(
      `SELECT video_id, channel, title, url, published_at, analysis
       FROM podcast_episodes
       WHERE status = 'summarized' AND analysis IS NOT NULL
         AND published_at IS NOT NULL
         AND (published_at AT TIME ZONE 'UTC')::date = $1::date
       ORDER BY published_at DESC`,
      [date]
    );
    return rows.map(r => ({
      ...r,
      published_at: r.published_at ? r.published_at.toISOString() : null,
    }));
  } catch {
    return [];
  }
}

// List of recent weekly digests for the sidebar (week_start, week_end, rotation, content).
export async function listWeeklyDigests(limit = 10) {
  try {
    const { rows } = await pool.query(
      `SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
              to_char(week_end,'YYYY-MM-DD')   AS week_end,
              rotation, content,
              to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
       FROM weekly_digest
       WHERE error IS NULL
       ORDER BY week_start DESC
       LIMIT $1`,
      [limit]
    );
    return rows;
  } catch {
    return [];
  }
}

// Weekly digest that covers the given date (null if none yet).
export async function getWeeklyForDate(date) {
  try {
    const { rows } = await pool.query(
      `SELECT to_char(week_start,'YYYY-MM-DD') AS week_start,
              to_char(week_end,'YYYY-MM-DD')   AS week_end,
              content, rotation,
              to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
       FROM weekly_digest
       WHERE error IS NULL AND week_start <= $1::date AND week_end >= $1::date
       ORDER BY week_start DESC
       LIMIT 1`,
      [date]
    );
    return rows[0] ?? null;
  } catch {
    return null;
  }
}

// Pre-computed analyst views + importance scores for all bullets of a digest date.
// Returns { [title]: { analysis, importanceScore, sourceCount } } — empty object if none yet.
export async function getBulletAnalyses(date) {
  try {
    const { rows } = await pool.query(
      `SELECT title, analysis, importance_score, source_count
         FROM digest_bullet_analysis WHERE digest_date = $1::date`,
      [date]
    );
    return Object.fromEntries(rows.map(r => [r.title, {
      analysis:       r.analysis,
      importanceScore: r.importance_score,
      sourceCount:     r.source_count,
    }]));
  } catch {
    return {};
  }
}

// Source publish times for digest bullets, keyed by their source link.
// Bullet links are stored verbatim from feed_items, so an exact-link join resolves
// each bullet's article pub_date. Returns { [link]: ISO-8601 string } — empty on miss/error.
export async function getBulletTimes(links) {
  const uniq = [...new Set((links || []).filter(Boolean))];
  if (!uniq.length) return {};
  try {
    const { rows } = await pool.query(
      `SELECT link, pub_date FROM feed_items
        WHERE link = ANY($1::text[]) AND pub_date IS NOT NULL`,
      [uniq]
    );
    return Object.fromEntries(
      rows.map(r => [r.link, r.pub_date ? r.pub_date.toISOString() : null])
    );
  } catch {
    return {};
  }
}

// Pre-computed entity intel brief — returns fresh row (within 7 days) or null.
// Checks by canonical name first, then falls back to alias lookup via entity_memory.
export async function getEntityIntelBrief(query) {
  if (!query) return null;
  try {
    const { rows } = await pool.query(
      `SELECT entity_name, brief_html, digest_date
       FROM entity_intel_brief
       WHERE lower(entity_name) = lower($1)
         AND digest_date >= CURRENT_DATE - 7
       LIMIT 1`,
      [query]
    );
    if (rows[0]) return rows[0];

    // Alias lookup via entity_memory
    const { rows: em } = await pool.query(
      `SELECT name FROM entity_memory
       WHERE lower(name) = lower($1)
          OR lower($1) = ANY(SELECT lower(a) FROM unnest(aliases) AS a)
       LIMIT 1`,
      [query]
    );
    if (!em[0]) return null;

    const { rows: rows2 } = await pool.query(
      `SELECT entity_name, brief_html, digest_date
       FROM entity_intel_brief
       WHERE lower(entity_name) = lower($1)
         AND digest_date >= CURRENT_DATE - 7
       LIMIT 1`,
      [em[0].name]
    );
    return rows2[0] ?? null;
  } catch {
    return null;
  }
}

// Find protocols whose name (or first word) appears as a whole word in the headline.
// \y is PostgreSQL's word-boundary assertion — prevents "Meta" matching "MetaDAO",
// "Free" matching "freeze", etc. SQL replace() escapes . and + in names like ether.fi.
// Both branches use ~* (case-insensitive) so "chainlink" matches "Chainlink".
export async function searchProjectInfo(text) {
  if (!text) return { protocols: [] };
  try {
    const { rows } = await pool.query(
      `SELECT slug, name, category, chains, chain_tvls, tvl_usd::float8, tvl_change_1d::float8,
              url, logo_url, token_symbol, gecko_id
       FROM defillama_protocols
       WHERE (
         -- full name: word-boundary case-insensitive match
         -- excludes single-word protocols whose name is a common English word
         $1 ~* ('\\y' || replace(replace(name, '.', '[.]'), '+', '[+]') || '\\y')
         AND lower(name) != ALL(ARRAY['idle','free','chain','token','tokens','network',
                                      'protocol','finance','open','world','fun',
                                      'across','capital','yield','basis','standard',
                                      'bridge','native','wrapped','labs','group',
                                      'vault','vaults','credit','lending','stable',
                                      'stablecoin','savings','staking','staked','liquid',
                                      'restaking','perp','perps','core','pool','prime',
                                      'real','spot','treasury','rewards','points','public',
                                      'strategy','movement','bullish'])
         OR (
           -- first word of multi-word name, length ≥ 4, not a generic word.
           -- The stop-list mirrors entities.GENERIC_TERMS so junk sub-products don't
           -- piggyback on a generic prefix: "Yield Basis" must not tag every "yield"
           -- headline, "Vault Bridge" every "vault", "Credit Coop" every "credit",
           -- "Lending by AlphaFi" every "lending".
           name LIKE '% %'
           AND length(split_part(name, ' ', 1)) >= 4
           AND lower(split_part(name, ' ', 1)) != ALL(ARRAY['chain','free','idle','defi',
                                                             'token','tokens','world','open',
                                                             'blockchain','protocol','digital',
                                                             'crypto','capital','finance',
                                                             'decentralized','global','network',
                                                             'native','wrapped','circle',
                                                             'yield','vault','vaults','credit',
                                                             'lending','stable','stablecoin',
                                                             'savings','staking','staked','liquid',
                                                             'restaking','perp','perps','basis',
                                                             'core','pool','prime','real','spot',
                                                             'treasury','rewards','points','public',
                                                             'strategy','movement','bullish'])
           AND $1 ~* ('\\y' || replace(replace(split_part(name, ' ', 1), '.', '[.]'), '+', '[+]') || '\\y')
         )
       )
       -- Suppress canonical bridges unless the text explicitly mentions "bridge"
       AND NOT (category = 'Canonical Bridge' AND $1 !~* '\\ybridge\\y')
       ORDER BY tvl_usd DESC
       LIMIT 5`,
      [text]
    );
    return { protocols: rows };
  } catch {
    return { protocols: [] };
  }
}

// Find entity_memory entities whose name or aliases match the text as whole words.
// Three matching paths:
//   1. Alias match          — alias length ≥ 4, not a stop-word
//   2. First-word of multi-word name — first word ≥ 6 chars (prevents "Aave Labs"→"Aave",
//      "Base Commerce"→"Base" false positives; keeps "Snapshot"→"Snapshot Labs")
//   3. Short distinctive single-word names (3-5 chars) — type protocol/chain/dao only,
//      mention_count ≥ 10 (catches Arc, Sui, SP1; blocks SEC, ICE, etc.)
// type='other' is excluded entirely — those are news outlets, tools, regulators.
export async function searchEntityMemory(text) {
  if (!text) return [];
  try {
    const { rows } = await pool.query(
      `SELECT e.slug, e.name, e.type, e.twitter_handle,
              COALESCE(p.logo_url, e.logo_url) AS logo_url,
              p.category, p.tvl_usd::float8, p.gecko_id
       FROM entity_memory e
       LEFT JOIN defillama_protocols p ON p.slug = e.slug
       WHERE e.mention_count >= 2
         AND e.type NOT IN ('other')
         AND (
           -- Path 1: alias word-boundary match (≥4 chars, not a stop-word)
           EXISTS (
             SELECT 1 FROM unnest(e.aliases) AS alias
             WHERE length(alias) >= 4
               AND alias NOT LIKE '@%'
               AND lower(alias) != ALL(ARRAY['chain','free','idle','defi','token','tokens',
                                             'network','protocol','finance','open','world',
                                             'new','core','main','node','fund','labs',
                                             'across','yield','capital','basis','group',
                                             'standard','bridge','native','push'])
               AND $1 ~* ('\\y' || replace(replace(alias, '.', '[.]'), '+', '[+]') || '\\y')
           )
           -- Path 2: first word of multi-word name, length ≥ 6, not a generic word
           -- (Snapshot→"Snapshot Labs", LayerZero→"LayerZero Core", etc.)
           -- Excludes overly generic first words that appear in every article
           OR (
             e.name LIKE '% %'
             AND length(split_part(e.name, ' ', 1)) >= 6
             AND lower(split_part(e.name, ' ', 1)) != ALL(ARRAY['protocol','blockchain',
                                                                  'digital','crypto','capital',
                                                                  'finance','network','global',
                                                                  'decentralized','standard'])
             AND $1 ~* ('\\y' || replace(replace(split_part(e.name, ' ', 1), '.', '[.]'), '+', '[+]') || '\\y')
           )
           -- Path 3: short single-word distinctive names (Arc, Sui, SP1)
           OR (
             e.name NOT LIKE '% %'
             AND length(e.name) BETWEEN 3 AND 5
             AND e.mention_count >= 10
             AND e.type IN ('protocol', 'chain', 'dao', 'exchange', 'fund')
             AND $1 ~* ('\\y' || replace(replace(e.name, '.', '[.]'), '+', '[+]') || '\\y')
           )
         )
       ORDER BY e.mention_count DESC
       LIMIT 6`,
      [text]
    );
    return rows;
  } catch {
    return [];
  }
}

// ── Narratives ──────────────────────────────────────────────────────────────
// Resolve entity slugs → display objects (name, logo, type) for chips.
export async function resolveEntities(slugs) {
  const list = [...new Set((slugs || []).filter(Boolean))];
  if (!list.length) return {};
  try {
    const { rows } = await pool.query(
      `SELECT e.slug, e.name, e.type, e.twitter_handle,
              COALESCE(p.logo_url, e.logo_url) AS logo_url,
              p.slug AS protocol_slug, p.tvl_usd::float8 AS tvl_usd
       FROM entity_memory e
       LEFT JOIN defillama_protocols p ON p.slug = e.slug
       WHERE e.slug = ANY($1)`,
      [list]
    );
    return Object.fromEntries(rows.map(r => [r.slug, r]));
  } catch {
    return {};
  }
}

const NARR_COLS = `slug, label, thesis, watch_next, contrarian, entity_slugs, state,
  intensity_48h::float8 AS intensity_48h, baseline::float8 AS baseline,
  momentum_ratio::float8 AS momentum_ratio, delta_48h, signal_count,
  dominant_type, severity,
  to_char(first_seen,'YYYY-MM-DD') AS first_seen,
  to_char(last_signal_at,'YYYY-MM-DD"T"HH24:MI:SS') AS last_signal_at`;

const STATE_ORDER = `array_position(ARRAY['heating','forming','steady','cooling','dormant']::text[], state)`;

// All narratives for the Pulse board, ranked (heating/forming first, then momentum).
// `includeDormant=false` hides dormant from the board (still reachable by slug).
export async function getNarratives({ includeDormant = false } = {}) {
  try {
    const { rows } = await pool.query(
      `SELECT ${NARR_COLS} FROM narratives
       ${includeDormant ? "" : "WHERE state <> 'dormant'"}
       ORDER BY ${STATE_ORDER}, momentum_ratio DESC NULLS LAST`
    );
    const ents = await resolveEntities(rows.flatMap(r => r.entity_slugs || []));
    return rows.map(r => ({
      ...r,
      entities: (r.entity_slugs || []).map(s => ents[s]).filter(Boolean),
    }));
  } catch {
    return [];
  }
}

// One narrative + its evidence timeline (capped) + resolved entities.
// News signals are LEFT-JOINed to their cached analyst view for the inspector.
export async function getNarrative(slug) {
  try {
    const { rows } = await pool.query(
      `SELECT ${NARR_COLS} FROM narratives WHERE slug = $1`, [slug]
    );
    const n = rows[0];
    if (!n) return null;
    const { rows: sig } = await pool.query(
      `SELECT s.signal_type, s.signal_ref, s.title, s.body, s.url,
              s.importance, to_char(s.ts,'YYYY-MM-DD"T"HH24:MI:SS') AS ts,
              a.analysis, a.source_count
       FROM narrative_signals s
       LEFT JOIN LATERAL (
         SELECT analysis, source_count FROM digest_bullet_analysis a
         WHERE s.signal_type = 'news' AND a.title = s.title
         ORDER BY a.digest_date DESC LIMIT 1
       ) a ON true
       WHERE s.narrative_slug = $1
       ORDER BY s.ts DESC NULLS LAST
       LIMIT 28`,
      [slug]
    );
    const ents = await resolveEntities(n.entity_slugs || []);
    return {
      ...n,
      entities: (n.entity_slugs || []).map(s => ents[s]).filter(Boolean),
      signals: sig,
    };
  } catch {
    return null;
  }
}

// All board narratives, each with its (capped) evidence timeline — for the sidebar
// section + RightPanel NarrativePanel (mirrors how weekly/podcast pass full objects).
export async function getNarrativesWithSignals({ includeDormant = false } = {}) {
  const narrs = await getNarratives({ includeDormant });
  if (!narrs.length) return [];
  try {
    const { rows } = await pool.query(
      `SELECT s.narrative_slug, s.signal_type, s.signal_ref, s.title, s.body, s.url,
              s.importance, to_char(s.ts,'YYYY-MM-DD"T"HH24:MI:SS') AS ts,
              a.analysis, a.source_count
       FROM narrative_signals s
       LEFT JOIN LATERAL (
         SELECT analysis, source_count FROM digest_bullet_analysis a
         WHERE s.signal_type = 'news' AND a.title = s.title
         ORDER BY a.digest_date DESC LIMIT 1
       ) a ON true
       WHERE s.narrative_slug = ANY($1)
       ORDER BY s.ts DESC NULLS LAST`,
      [narrs.map(n => n.slug)]
    );
    const bySlug = {};
    for (const s of rows) (bySlug[s.narrative_slug] ??= []).push(s);
    return narrs.map(n => ({ ...n, signals: (bySlug[n.slug] || []).slice(0, 24) }));
  } catch {
    return narrs.map(n => ({ ...n, signals: [] }));
  }
}

// ── Narrative entity graph ───────────────────────────────────────────────────
// Nodes = entities that appear in narrative clusters; edges = two entities that
// co-appear in the *same* narrative. The node set is intrinsically bounded by the
// narrative layer (entity_slugs across ~10 narratives ⇒ ~40 nodes), so there is no
// hairball risk even though entity_memory itself is large — we never read the full
// entity table, only the slugs the narratives reference.
//
// Edge weight = number of shared narratives. We keep ALL co-appearance edges
// (min weight 1): in the live data every pair shares exactly one narrative, so a
// ≥2 threshold would render zero edges. The weight is still carried so that, as the
// data grows, pairs that recur across clusters render as thicker links.
//
// Each node carries its representative momentum `state` = the hottest state among
// the narratives it belongs to (heating ▸ forming ▸ steady ▸ cooling ▸ dormant),
// so an entity glows with its most active story. Dormant narratives are included
// here; the client hides them by default and can toggle them on (no refetch).
//
// Returns { nodes, edges } — plain JSON-serializable objects, safe to cross the
// server→client boundary and to wrap in `unstable_cache`.
export async function getNarrativeGraph({ includeDormant = true } = {}) {
  const STATE_PRIO = { heating: 4, forming: 3, steady: 2, cooling: 1, dormant: 0 };
  try {
    const { rows: narrs } = await pool.query(
      `SELECT slug, label, state, signal_count,
              momentum_ratio::float8 AS momentum_ratio, delta_48h, entity_slugs
       FROM narratives
       ${includeDormant ? "" : "WHERE state <> 'dormant'"}
       ORDER BY ${STATE_ORDER}, momentum_ratio DESC NULLS LAST`
    );
    const slugs = [...new Set(narrs.flatMap(n => n.entity_slugs || []))];
    if (!slugs.length) return { nodes: [], edges: [] };

    const { rows: ents } = await pool.query(
      `SELECT e.slug, e.name, e.type, e.mention_count, e.twitter_handle, e.summary,
              COALESCE(p.logo_url, e.logo_url) AS logo_url
       FROM entity_memory e
       LEFT JOIN defillama_protocols p ON p.slug = e.slug
       WHERE e.slug = ANY($1)`,
      [slugs]
    );
    const bySlug = Object.fromEntries(ents.map(e => [e.slug, e]));

    // Build nodes — only entities that actually exist in entity_memory become nodes.
    const nodeMap = new Map();
    for (const n of narrs) {
      for (const s of n.entity_slugs || []) {
        const e = bySlug[s];
        if (!e) continue;
        let node = nodeMap.get(s);
        if (!node) {
          node = {
            slug: s,
            name: e.name,
            type: e.type,
            mentionCount: e.mention_count ?? 0,
            twitterHandle: e.twitter_handle || null,
            summary: e.summary || null,
            logoUrl: e.logo_url || null,
            state: n.state,
            narratives: [],
          };
          nodeMap.set(s, node);
        }
        if ((STATE_PRIO[n.state] ?? -1) > (STATE_PRIO[node.state] ?? -1)) node.state = n.state;
        node.narratives.push({
          slug: n.slug,
          label: n.label,
          state: n.state,
          signalCount: n.signal_count,
          momentumRatio: n.momentum_ratio,
          delta: n.delta_48h,
        });
      }
    }

    // Build edges — co-appearance within a narrative, deduped + weighted.
    const edgeMap = new Map();
    for (const n of narrs) {
      const present = (n.entity_slugs || []).filter(s => nodeMap.has(s));
      for (let i = 0; i < present.length; i++) {
        for (let j = i + 1; j < present.length; j++) {
          const [a, b] = present[i] < present[j]
            ? [present[i], present[j]]
            : [present[j], present[i]];
          const key = `${a}|${b}`;
          let edge = edgeMap.get(key);
          if (!edge) { edge = { source: a, target: b, weight: 0, narratives: [] }; edgeMap.set(key, edge); }
          edge.weight += 1;
          edge.narratives.push({ slug: n.slug, label: n.label, state: n.state });
        }
      }
    }

    return { nodes: [...nodeMap.values()], edges: [...edgeMap.values()] };
  } catch {
    return { nodes: [], edges: [] };
  }
}

// ── Entity co-occurrence graph (the "all entities" map) ──────────────────────
// Reads the precomputed entity_edges (built by app/entity_graph.py). Returns the
// top `maxNodes` entities by mention_count that have at least one edge (weight ≥
// minWeight), enriched with type / TVL / logo / momentum state, plus every edge
// among them. The client filters by edge weight + type + search with no refetch.
export async function getEntityGraph({ maxNodes = 240, minWeight = 2 } = {}) {
  try {
    const { rows: nodeRows } = await pool.query(
      `WITH ew AS (
         SELECT slug_a, slug_b, weight FROM entity_edges WHERE weight >= $1
       ),
       deg AS (
         SELECT slug, sum(weight)::int AS strength, count(*)::int AS degree
         FROM (SELECT slug_a AS slug, weight FROM ew
               UNION ALL SELECT slug_b, weight FROM ew) u
         GROUP BY slug
       )
       SELECT e.slug, e.name, e.type, e.mention_count, e.summary, e.twitter_handle,
              COALESCE(p.logo_url, e.logo_url) AS logo_url,
              p.tvl_usd::float8 AS tvl_usd, p.category,
              d.strength, d.degree, n.state AS narrative_state
       FROM deg d
       JOIN entity_memory e ON e.slug = d.slug
       LEFT JOIN defillama_protocols p ON p.slug = e.slug
       LEFT JOIN LATERAL (
         SELECT state FROM narratives
         WHERE d.slug = ANY(entity_slugs)
         ORDER BY array_position(
           ARRAY['heating','forming','steady','cooling','dormant']::text[], state)
         LIMIT 1
       ) n ON true
       ORDER BY e.mention_count DESC NULLS LAST
       LIMIT $2`,
      [minWeight, maxNodes]
    );
    if (!nodeRows.length) return { nodes: [], edges: [] };

    const slugs = nodeRows.map((r) => r.slug);
    const { rows: edgeRows } = await pool.query(
      `SELECT slug_a, slug_b, weight, npmi, examples FROM entity_edges
        WHERE weight >= $1 AND slug_a = ANY($2) AND slug_b = ANY($2)`,
      [minWeight, slugs]
    );

    const nodes = nodeRows.map((r) => ({
      slug: r.slug,
      name: r.name,
      type: r.type || "other",
      mentionCount: r.mention_count ?? 0,
      summary: r.summary || null,
      twitterHandle: r.twitter_handle || null,
      logoUrl: r.logo_url || null,
      tvl: r.tvl_usd ?? null,
      category: r.category || null,
      degree: r.degree ?? 0,
      strength: r.strength ?? 0,
      narrativeState: r.narrative_state || null,
    }));
    const edges = edgeRows.map((e) => ({
      source: e.slug_a, target: e.slug_b, weight: e.weight,
      npmi: e.npmi == null ? 0 : Number(e.npmi),
      examples: Array.isArray(e.examples) ? e.examples.slice(0, 3) : [],
    }));
    return { nodes, edges };
  } catch {
    return { nodes: [], edges: [] };
  }
}

// Latest two TVL readings per chain; computes day-over-day % change.
export async function getTvlWithChange() {
  try {
    const { rows } = await pool.query(
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
  } catch {
    return [];
  }
}
