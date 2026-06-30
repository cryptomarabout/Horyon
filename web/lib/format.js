// Shared display formatters — keep ONE definition per format so every surface
// (feed, right panel, entity map …) renders numbers/dates identically.

// Compact USD for TVL / market-cap: $1.23T · $4.5B · $678M · $12,345.
// Returns null for nullish or non-positive input so callers can skip rendering.
export function fmtTvl(usd) {
  if (usd == null) return null;
  if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
  if (usd >= 1e9)  return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6)  return `$${(usd / 1e6).toFixed(0)}M`;
  if (usd > 0)     return `$${usd.toLocaleString()}`;
  return null;
}

// Short relative time with an "ago" suffix: now · 12m ago · 3h ago · 2d ago.
export function fmtAgo(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const m = Math.floor((Date.now() - t) / 60000);
  if (m < 1)  return "now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// Canonical month abbreviations — single source of truth for all surfaces.
export const MONTHS_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

// "Jun 21" short date — for related article dates, evidence rows, etc.
export function fmtShortDate(iso) {
  const [, m, d] = (iso || "").split("-").map(Number);
  if (!m) return "";
  return `${MONTHS_SHORT[m - 1] ?? "?"} ${d}`;
}

// Elapsed-time with a date fallback: "today" | "1d ago" | "5d ago" | "Jun 21"
// Used in evidence rows where a raw offset matters more than clock precision.
export function fmtDayAgo(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (isNaN(t)) return null;
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  const d = new Date(t);
  return `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

// Week range: "Jun 5–11, 2026" or "May 28 – Jun 3, 2026"
export function fmtWeekRange(start, end) {
  const p = s => { const [y, m, d] = (s || "").split("-").map(Number); return { y, m, d }; };
  const s = p(start), e = p(end);
  if (!s.y) return "";
  if (s.m === e.m) return `${MONTHS_SHORT[s.m - 1]} ${s.d}–${e.d}, ${s.y}`;
  return `${MONTHS_SHORT[s.m - 1]} ${s.d} – ${MONTHS_SHORT[e.m - 1]} ${e.d}, ${s.y}`;
}

// Hostname from URL, strips www prefix.
export function getDomain(url) {
  if (!url) return null;
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return null; }
}
