// Shared entity-graph taxonomy (pure data — safe in server or client components).
// Node color encodes entity type; the legend doubles as the type filter.

export const TYPE_META = {
  protocol: { label: "Protocols", cls: "protocol" },
  chain:    { label: "Chains",    cls: "chain" },
  exchange: { label: "Exchanges", cls: "exchange" },
  fund:     { label: "Funds",     cls: "fund" },
  person:   { label: "People",    cls: "person" },
  other:    { label: "Other",     cls: "other" },
};

// DAOs are folded into Protocols (see getEntityGraph) — no separate "dao" lens.
export const TYPES = ["protocol", "chain", "exchange", "fund", "person", "other"];

// ── Lenses & layouts (pure config, shared by the graph root + its toolbar) ────
// Two lenses on the same edges:
//  · Affinity (NPMI) — how much MORE than chance two entities co-occur. Surfaces
//    real relationships; ubiquitous hubs (Bitcoin) recede. The default.
//  · Volume — raw co-mention count. Most-discussed-together; hubs dominate.
export const METRICS = {
  affinity: { label: "Affinity", levels: [0.1, 0.25, 0.4, 0.55], names: ["All", "Med", "High", "Tight"],
              hint: "How much more than chance two entities are mentioned together" },
  volume:   { label: "Volume",   levels: [2, 3, 4, 6],          names: ["All", "3+", "4+", "Strong"],
              hint: "Raw number of articles mentioning both entities" },
};
export const DEFAULT_METRIC = "affinity";
export const DEFAULT_LEVEL = 1;
// Index — the ranked entity screener (default) ⇄ Board (carded clusters) ⇄ Network.
export const DEFAULT_VIEW = "table";

// ── Geometry / id helpers (pure — used by both D3 renderers) ──────────────────
export const R_MIN = 8, R_MAX = 26;            // board (capped to grid cell)
export const R_MIN_NET = 6, R_MAX_NET = 30;    // network (free-floating, can run bigger)

// Coin radius from mention count. `cap` (board only) keeps coins inside their cell.
export function radiusFor(mc, minMc, maxMc, cap, rMin = R_MIN, rMax = R_MAX) {
  let r;
  if (maxMc <= minMc) r = (rMin + rMax) / 2;
  else {
    const t = (Math.sqrt(Math.max(mc, minMc)) - Math.sqrt(minMc)) /
              (Math.sqrt(maxMc) - Math.sqrt(minMc));
    r = rMin + t * (rMax - rMin);
  }
  return cap == null ? r : Math.max(6, Math.min(r, cap));
}

export { monogram } from "./format.js";

export const edgeKey = (s, t) => (s < t ? `${s}|${t}` : `${t}|${s}`);
export const safeId = (s) => `egclip-${String(s).replace(/[^a-z0-9_-]/gi, "_")}`;

// Gentle, consistently-bowed arc between two points — reads cleaner than a straight
// line and keeps the cross-region links legible.
export function arcPath(x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const cx = (x1 + x2) / 2 - dy * 0.1;
  const cy = (y1 + y2) / 2 + dx * 0.1;
  return `M${x1},${y1}Q${cx},${cy} ${x2},${y2}`;
}

// Avatar fallback chain for a node: the bot-mirrored avatar served from our own DB
// (/api/avatar/<slug> — no client stampede, no external call) → the original real logo
// (DeFiLlama/CoinGecko) → the Twitter avatar via unavatar.io (aggregates Nitter/X/etc.)
// → monogram (handled by the caller when the list is exhausted). `fallback=false` makes
// unavatar 404 when it has no picture, so we drop to the monogram instead of its grey blob.
export function avatarCandidates(node) {
  const out = [];
  if (node?.avatarCached && node?.slug) out.push(`/api/avatar/${encodeURIComponent(node.slug)}`);
  if (node?.logoUrl) out.push(node.logoUrl);
  const h = (node?.twitterHandle || "").replace(/^@/, "").trim();
  if (h) out.push(`https://unavatar.io/twitter/${encodeURIComponent(h)}?fallback=false`);
  return out;
}
