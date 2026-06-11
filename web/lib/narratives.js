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

// Momentum ratio → directional arrow + tier (▲▲ ρ≥2 · ▲ 1.5≤ρ<2 · ➜ steady · ▼ ρ<0.7).
export function momentumArrow(rho) {
  if (rho == null) return { arrow: "➜", dir: "flat" };
  if (rho >= 2)   return { arrow: "▲▲", dir: "up" };
  if (rho >= 1.5) return { arrow: "▲",  dir: "up" };
  if (rho < 0.7)  return { arrow: "▼",  dir: "down" };
  return { arrow: "➜", dir: "flat" };
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

// Delta badge text, e.g. "+18 / 48h" or "new".
export function deltaLabel(delta, state) {
  if (state === "forming" && (delta == null || delta <= 0)) return "new";
  const n = delta || 0;
  const sign = n > 0 ? "+" : "";
  return `${sign}${n} / 48h`;
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

// Evidence-type counts for a narrative's signal list → ordered [{type, n, glyph}].
export function evidenceCounts(signals) {
  const tally = {};
  for (const s of signals || []) {
    tally[s.signal_type] = (tally[s.signal_type] || 0) + 1;
  }
  return ["news", "podcast", "governance", "market"]
    .filter(t => tally[t])
    .map(t => ({ type: t, n: tally[t], ...typeMeta(t) }));
}

// Score tier for the importance ring (mirrors BulletItem.scoreTier).
export function scoreTier(score) {
  if (score >= 80) return "hi";
  if (score >= 50) return "mid";
  if (score >= 20) return "lo";
  return "min";
}
