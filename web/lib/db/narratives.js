// app/lib/db/narratives.js — narrative board/inspector + entity co-occurrence graph
import { pool } from "./_core.js";
import { resolveEntities } from "./entities.js";
import { deriveSector, distinctDomains } from "../narratives.js";


const NARR_COLS = `slug, label, thesis, watch_next, contrarian, entity_slugs, state,
  intensity_48h::float8 AS intensity_48h, baseline::float8 AS baseline,
  momentum_ratio::float8 AS momentum_ratio, delta_48h, signal_count,
  dominant_type, severity,
  to_char(first_seen,'YYYY-MM-DD') AS first_seen,
  to_char(last_signal_at,'YYYY-MM-DD"T"HH24:MI:SS') AS last_signal_at`;


const STATE_ORDER = `array_position(ARRAY['heating','forming','steady','cooling','dormant']::text[], state)`;


// The Research redesign adds sector/source_count/key_points. The migration is
// applied out-of-band (deploy/migrations/2026-06-25_research_narratives.sql), so the
// web layer must render correctly BEFORE it lands: probe which optional columns exist
// (once, memoised) and only SELECT the ones present — then derive sensible fallbacks
// for the rest. Naming a missing column would error the whole query, so this guard is
// what keeps the page alive across the migration boundary.
let _narrOptCols = null;
async function narrativeOptionalCols() {
  if (_narrOptCols) return _narrOptCols;
  try {
    const { rows } = await pool.query(
      `SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'narratives'
          AND column_name = ANY($1)`,
      [["sector", "source_count", "key_points"]]
    );
    _narrOptCols = new Set(rows.map(r => r.column_name));
  } catch {
    _narrOptCols = new Set();
  }
  return _narrOptCols;
}
async function narrSelectCols() {
  const opt = await narrativeOptionalCols();
  const extra = ["sector", "source_count", "key_points"].filter(c => opt.has(c));
  return extra.length ? `${NARR_COLS}, ${extra.join(", ")}` : NARR_COLS;
}

// Fill the research fields with stored-or-derived values so callers never branch on
// whether the migration has run yet.
function enrichNarrative(r) {
  return {
    ...r,
    sector: r.sector || deriveSector(r.label, r.thesis, r.entity_slugs),
    key_points: r.key_points || [],
  };
}


// All narratives for the Pulse board, ranked (heating/forming first, then momentum).
// `includeDormant=false` hides dormant from the board (still reachable by slug).
export async function getNarratives({ includeDormant = false } = {}) {
  try {
    const cols = await narrSelectCols();
    const { rows } = await pool.query(
      `SELECT ${cols} FROM narratives
       ${includeDormant ? "" : "WHERE state <> 'dormant'"}
       ORDER BY ${STATE_ORDER}, momentum_ratio DESC NULLS LAST`
    );
    const ents = await resolveEntities(rows.flatMap(r => r.entity_slugs || []));
    return rows.map(r => ({
      ...enrichNarrative(r),
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
    const cols = await narrSelectCols();
    const { rows } = await pool.query(
      `SELECT ${cols} FROM narratives WHERE slug = $1`, [slug]
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
      ...enrichNarrative(n),
      entities: (n.entity_slugs || []).map(s => ents[s]).filter(Boolean),
      source_count: n.source_count ?? distinctDomains(sig),
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
    return narrs.map(n => {
      const signals = (bySlug[n.slug] || []).slice(0, 24);
      return { ...n, signals, source_count: n.source_count ?? distinctDomains(signals) };
    });
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
              p.tvl_change_1d::float8 AS tvl_change_1d,
              p.tvl_change_7d::float8 AS tvl_change_7d,
              p.mcap_tvl::float8 AS mcap_tvl,
              p.token_symbol, p.chains,
              m.price_usd::float8 AS price_usd, m.market_cap_usd::float8 AS market_cap_usd,
              m.fdv_usd::float8 AS fdv_usd, m.circulating_supply::float8 AS circulating_supply,
              m.total_supply::float8 AS total_supply,
              m.price_change_7d_pct::float8 AS price_change_7d_pct,
              d.strength, d.degree, n.state AS narrative_state,
              (av.slug IS NOT NULL) AS avatar_cached
       FROM deg d
       JOIN entity_memory e ON e.slug = d.slug
       LEFT JOIN defillama_protocols p ON p.slug = e.slug
       LEFT JOIN entity_avatars av ON av.slug = e.slug
       LEFT JOIN coingecko_market m
         ON m.gecko_id = e.slug AND m.fetched_at > now() - INTERVAL '3 days'
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
      // DAOs are folded into Protocols on the map — they're not a distinct lens.
      type: r.type === "dao" ? "protocol" : (r.type || "other"),
      mentionCount: r.mention_count ?? 0,
      summary: r.summary || null,
      twitterHandle: r.twitter_handle || null,
      logoUrl: r.logo_url || null,
      avatarCached: r.avatar_cached === true,
      tvl: r.tvl_usd ?? null,
      tvlChange1d: r.tvl_change_1d ?? null,
      tvlChange7d: r.tvl_change_7d ?? null,
      mcapTvl: r.mcap_tvl ?? null,
      price: r.price_usd ?? null,
      marketCap: r.market_cap_usd ?? null,
      fdv: r.fdv_usd ?? null,
      circulatingSupply: r.circulating_supply ?? null,
      totalSupply: r.total_supply ?? null,
      priceChange7d: r.price_change_7d_pct ?? null,
      tokenSymbol: r.token_symbol || null,
      chains: Array.isArray(r.chains) ? r.chains : [],
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
