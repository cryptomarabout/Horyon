// Shared narrative presentation helpers (pure — safe in server or client components).

// State → glyph + label + css modifier. Drives the board row + dossier header.
export const STATE_META = {
  heating: { glyph: "🔥", label: "heating", cls: "heating" },
  forming: { glyph: "🌱", label: "forming", cls: "forming" },
  steady:  { glyph: "➜", label: "steady",  cls: "steady"  },
  cooling: { glyph: "❄", label: "cooling", cls: "cooling" },
  dormant: { glyph: "·", label: "dormant", cls: "dormant" },
};

export function stateMeta(state) {
  return STATE_META[state] || STATE_META.steady;
}

// Evidence-type → compact glyph + label.
export const TYPE_META = {
  news:       { glyph: "📰", label: "News" },
  podcast:    { glyph: "🎙", label: "Podcast" },
  governance: { glyph: "🏛", label: "Governance" },
  market:     { glyph: "📈", label: "Market" },
};

export function typeMeta(t) {
  return TYPE_META[t] || TYPE_META.news;
}

// Severity → css modifier (mirrors the bullet severity bar palette).
export function severityCls(sev) {
  return `sev--${sev || "neutral"}`;
}

// Short relative time, from an ISO string stored as UTC (no trailing Z).
export function timeAgo(iso) {
  if (!iso) return null;
  try {
    const ms = Date.now() - new Date(iso + "Z").getTime();
    if (ms < 0) return "now";
    const m = Math.floor(ms / 60000);
    if (m < 1) return "now";
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h`;
    return `${Math.floor(h / 24)}d`;
  } catch {
    return null;
  }
}

// Score tier for the importance ring (mirrors BulletItem.scoreTier).
export function scoreTier(score) {
  if (score >= 80) return "hi";
  if (score >= 50) return "mid";
  if (score >= 20) return "lo";
  return "min";
}


// ── Research presentation layer ───────────────────────────────────────────────
// The Atlas map imports `stateMeta`/`momentumArrow` above and must stay untouched;
// the Research index + brief use the institutional vocabulary below instead. The
// framing rule throughout: lead with HONEST, defensible facts (developments tracked,
// coverage window, conviction-by-corroboration) — never an inflated source count.

// Momentum state → research trajectory. No emoji; a typographic direction marker
// drives the visual. heating/forming read as upward, cooling as downward.
export const TRAJECTORY = {
  heating: { label: "Accelerating", dir: "up",   cls: "accel" },
  forming: { label: "Developing",   dir: "up",   cls: "develop" },
  steady:  { label: "Established",  dir: "flat", cls: "established" },
  cooling: { label: "Moderating",   dir: "down", cls: "moderating" },
  dormant: { label: "Dormant",      dir: "flat", cls: "dormant" },
};
export function trajectoryMeta(state) {
  return TRAJECTORY[state] || TRAJECTORY.steady;
}

// Momentum ratio → compact multiple ("1.2×") or null. Direction reuses momentumArrow.
export function momentumMultiple(rho) {
  if (rho == null || !isFinite(rho)) return null;
  return `${rho.toFixed(rho >= 10 ? 0 : 1)}×`;
}

// Conviction = corroboration breadth + persistence. Deliberately derived ONLY from
// how many developments accumulated over how long — never from a source count the
// data can't honestly support. High / Moderate / Emerging.
export function convictionTier({ signalCount = 0, spanDays = 0 } = {}) {
  if (signalCount >= 12 && spanDays >= 14) return { key: "high",     label: "High" };
  if (signalCount >= 5  && spanDays >= 7)  return { key: "moderate", label: "Moderate" };
  return { key: "emerging", label: "Emerging" };
}

const _MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function _fmtMD(d) { return `${_MON[d.getUTCMonth()]} ${d.getUTCDate()}`; }

// Coverage window from first_seen (date string) + last_signal_at (ISO, stored w/o Z).
export function coverageWindow(firstSeen, lastSignalAt) {
  if (!firstSeen) return null;
  const start = new Date(firstSeen + "T00:00:00Z");
  if (isNaN(start.getTime())) return null;
  const end = lastSignalAt ? new Date(lastSignalAt + "Z") : new Date();
  const spanDays = Math.max(1, Math.round((end.getTime() - start.getTime()) / 86400000));
  const weeks = Math.max(1, Math.round(spanDays / 7));
  return {
    spanDays, weeks, start, end,
    sinceLabel: `Since ${_fmtMD(start)}`,
    rangeLabel: `${_fmtMD(start)} – ${_fmtMD(end)}`,
    spanLabel: spanDays >= 14 ? `${weeks} wks` : `${spanDays}d`,
  };
}

// "Updated 2h ago" recency from last_signal_at (reuses timeAgo's UTC handling).
export function asOfLabel(lastSignalAt) {
  const ago = timeAgo(lastSignalAt);
  return ago ? `Updated ${ago} ago` : null;
}

// Evidence composition by signal type → {news, podcast, …}.
export function evidenceMix(signals) {
  const m = {};
  for (const s of signals || []) m[s.signal_type] = (m[s.signal_type] || 0) + 1;
  return m;
}
export function evidenceMixLabel(signals) {
  const m = evidenceMix(signals);
  return ["news", "podcast", "governance", "market"]
    .filter(t => m[t])
    .map(t => `${m[t]} ${t}`)
    .join(" · ");
}

// Distinct normalised source domains (honest breadth fallback when source_count
// isn't stored yet). x.com / nitter collapse to one, mirroring app/narratives.py.
export function distinctDomains(signals) {
  const set = new Set();
  for (const s of signals || []) {
    if (!s.url) continue;
    try {
      let h = new URL(s.url).hostname.replace(/^www\./, "");
      if (/nitter\./.test(h) || h === "x.com" || h === "twitter.com") h = "x.com";
      set.add(h);
    } catch { /* skip malformed */ }
  }
  return set.size;
}

// Activity cadence for the sparkline: development counts in `buckets` even bins
// across [first_seen, now]. Pure data — the SVG is drawn client-side.
export function cadenceSeries(signals, firstSeen, buckets = 12) {
  const times = (signals || [])
    .map(s => (s.ts ? new Date(s.ts + "Z").getTime() : NaN))
    .filter(t => !isNaN(t));
  if (!times.length) return [];
  const start = firstSeen ? new Date(firstSeen + "T00:00:00Z").getTime() : Math.min(...times);
  const end = Math.max(Date.now(), ...times);
  const span = Math.max(1, end - start);
  const bins = new Array(buckets).fill(0);
  for (const t of times) {
    let i = Math.floor(((t - start) / span) * buckets);
    if (i < 0) i = 0;
    if (i >= buckets) i = buckets - 1;
    bins[i] += 1;
  }
  return bins;
}

// Date labels (one per bucket) for sparkline tooltip — mirrors cadenceSeries span.
export function cadenceLabels(signals, firstSeen, buckets = 12) {
  const times = (signals || [])
    .map(s => (s.ts ? new Date(s.ts + "Z").getTime() : NaN))
    .filter(t => !isNaN(t));
  const start = firstSeen
    ? new Date(firstSeen + "T00:00:00Z").getTime()
    : (times.length ? Math.min(...times) : Date.now() - buckets * 86400000);
  const end = times.length ? Math.max(Date.now(), ...times) : Date.now();
  const span = Math.max(1, end - start);
  return Array.from({ length: buckets }, (_, i) => {
    const mid = new Date(start + (i + 0.5) * (span / buckets));
    return `${_MON[mid.getUTCMonth()]} ${mid.getUTCDate()}`;
  });
}

// ── Deterministic sector fallback (mirrors app/narratives.py:_sector) ─────────
// Only used when `sector` isn't stored yet (pre-migration / pre-rebuild); the
// pipeline value is authoritative once present. Scored: label ×3, slug ×2, thesis ×1.
const _SECTOR_FALLBACK = [
  ["RWA & Tokenization",      /\b(rwa|tokeniz|securitiz|t-?bill|treasury bill|ondo|vbill|buidl|private credit)/i],
  ["Stablecoins",            /\b(stablecoin|usdc|usdt|usde|ausd|pyusd|frxusd|gho|dai|tether|circle|ethena|depeg)/i],
  ["Staking & Restaking",    /\b(liquid stak|liquid restak|restak|lst|lrt|staking pool|steth|rseth)/i],
  ["Lending & Yield",        /\b(lend|borrow|yield|apy|apr|vault|collateral|morpho|aave|euler|pendle)/i],
  ["Derivatives",            /\b(perp|perpetual|option|futures|synthetic|basis trad|funding rate)/i],
  ["Cross-Chain & Interop",  /\b(bridg|cross[- ]chain|interop|omnichain|ccip|layerzero|wormhole|chainlink)/i],
  ["DEX & Trading",          /\b(dex|amm|swap|uniswap|curve|balancer|orderbook|market maker|liquidity pool)/i],
  ["Layer 2 & Scaling",      /\b(layer[- ]?2|l2|rollup|optimism|arbitrum|base|zk-?rollup|sequencer)/i],
  ["Layer 1 & Infrastructure", /\b(layer[- ]?1|l1|mainnet|validator|consensus|monad|arc|sei|sui|aptos|solana|avalanche)/i],
  ["Market Structure",       /\b(etf|custod|sec|mica|regulat|compliance|listing|cftc)/i],
  ["Governance",             /\b(governance|proposal|dao|tokenholder|vote)/i],
];
export function deriveSector(label, thesis, slugs) {
  const hayL = (label || "").toLowerCase();
  const hayT = (thesis || "").toLowerCase();
  const hayS = (slugs || []).join(" ").toLowerCase();
  let best = "DeFi", bestScore = 0;
  for (const [name, re] of _SECTOR_FALLBACK) {
    let sc = 0;
    if (re.test(hayL)) sc += 3;
    if (re.test(hayS)) sc += 2;
    if (re.test(hayT)) sc += 1;
    if (sc > bestScore) { best = name; bestScore = sc; }
  }
  return best;
}
