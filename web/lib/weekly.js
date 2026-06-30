// ── Weekly digest parsing — shared by the Weekly page reader (WeeklyView/
// WeeklyReader) and the legacy WeeklyPanel. Pure helpers only (no JSX) so they
// can be imported from both client components without duplication.

import { fmtWeekRange } from "./format.js";

// Rotation badge metadata — the week's dominant theme.
export const W_ROT = {
  BTC:   { glyph: "₿", cls: "rot-btc",   label: "BTC Week",   badgeCls: "pw-badge-btc" },
  ETH:   { glyph: "Ξ", cls: "rot-eth",   label: "ETH Week",   badgeCls: "pw-badge-eth" },
  ALT:   { glyph: "◈", cls: "rot-alt",   label: "Alt Week",   badgeCls: "pw-badge-alt" },
  MIXED: { glyph: "≋", cls: "rot-mixed", label: "Mixed Week", badgeCls: "pw-badge-mix" },
};
export const rotMeta = (r) => W_ROT[r] || W_ROT.MIXED;

// Section tabs, in display order. `id` is the section's leading emoji.
export const PW_TABS = [
  { id: "all", label: "All"      },
  { id: "🔥", label: "Trending" },
  { id: "📰", label: "Stories"  },
  { id: "📊", label: "Market"   },
  { id: "🔗", label: "DeFi"     },
  { id: "⚡", label: "Watch"    },
  { id: "🏆", label: "Movers"   },
];
export const TAB_ORDER = PW_TABS.filter(t => t.id !== "all").map(t => t.id);

const W_ENT = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };
export const wDecode = s => s.replace(/&[a-z#0-9]+;/gi, m => W_ENT[m] ?? m);

// Forbid em dashes (—, U+2014) in rendered report text: replace the spaced
// em-dash with a comma and tidy any doubled punctuation it creates. En dashes
// (–, U+2013, used in date ranges like "Jun 22–28") are deliberately untouched.
export const deDash = s =>
  (s || "")
    .replace(/\s*—\s*/g, ", ")
    .replace(/,\s*([,.;:])/g, "$1")
    .replace(/\(\s*,\s*/g, "(")
    .replace(/\s+,/g, ",");
const W_SEC_RE = /^<b>([📊🏆🔗🔥📰⚡][^<]*)<\/b>$/u;
export const stripSectionEmoji = s => s.replace(/^[📊🏆🔗🔥📰⚡]\s*/u, "").trim();

// Split the stored weekly HTML into { hdr, lines } sections.
export function parseWeeklySections(html) {
  if (!html) return [];
  const body = html.replace(/^📅\s*<b>[^<]*<\/b>\s*/i, "").trim();
  const out = [];
  let hdr = null, lines = [];
  for (const raw of body.split(/\n/)) {
    const t = raw.trim();
    const m = t.match(W_SEC_RE);
    if (m) { if (hdr !== null) out.push({ hdr, lines }); hdr = m[1].trim(); lines = []; }
    else if (hdr !== null && t) lines.push(t);
  }
  if (hdr !== null) out.push({ hdr, lines });
  return out;
}

// Filter + order sections for the active tab ("all" = canonical order).
export function sortVisibleSections(sections, activeTab) {
  if (activeTab === "all") {
    return [...sections].sort((a, b) => {
      const ai = TAB_ORDER.findIndex(id => a.hdr.startsWith(id));
      const bi = TAB_ORDER.findIndex(id => b.hdr.startsWith(id));
      return (ai < 0 ? TAB_ORDER.length : ai) - (bi < 0 ? TAB_ORDER.length : bi);
    });
  }
  return sections.filter(s => s.hdr.startsWith(activeTab));
}

// Which tabs actually have a section this week (so we never show an empty tab).
export function availableTabs(sections) {
  return PW_TABS.filter(t => t.id === "all" || sections.some(s => s.hdr.startsWith(t.id)));
}

// Section "tone" — drives the left-edge accent colour of its rows (echoes the
// Daily feed's severity bars). Keyed off the section's leading emoji.
export function sectionTone(hdr) {
  if (!hdr) return "other";
  if (hdr.startsWith("🔥")) return "trending";
  if (hdr.startsWith("📰")) return "stories";
  if (hdr.startsWith("📊")) return "market";
  if (hdr.startsWith("🔗")) return "defi";
  if (hdr.startsWith("⚡")) return "watch";
  if (hdr.startsWith("🏆")) return "movers";
  return "other";
}

export function colorizePcts(html) {
  return html.replace(/([▲▼]?\s*[+\-]\d+\.?\d*%)/g, (m) => {
    const stripped = m.replace(/^[▲▼]\s*/, "").trim();
    const isUp = stripped.startsWith("+");
    const tri  = isUp ? "▲" : "▼";
    const cls  = isUp ? "pct-up" : "pct-dn";
    return `<span class="${cls}">${tri}&thinsp;${stripped}</span>`;
  });
}

export function stripTrailingPeriod(html) {
  return html.replace(/\.\s*(<\/[^>]+>)?\s*$/, (_, tag) => tag || "");
}

// Movers: pull out TOKEN +N% / -N% pairs for chip rendering.
export function parseTokenPcts(html) {
  const text = html
    .replace(/<[^>]*>/g, "")
    .replace(/&[a-z#0-9]+;/gi, " ")
    .replace(/[‐‑‒–—−∕]/g, "-");
  // Ticker: uppercase start, then letters/digits/underscore (so symbols like
  // FIGR_HELOC, 0X0, RAIN parse), followed by a signed 7d %.
  const re = /\b([A-Z][A-Z0-9_]{1,11})\s+([+\-]\d+\.?\d*)\s*%/g;
  const pairs = [];
  let m;
  while ((m = re.exec(text)) !== null) pairs.push({ sym: m[1], pct: parseFloat(m[2]) });
  return pairs;
}

export function getMoverLabel(html) {
  const m = html.match(/<b>([^<]+?):?\s*<\/b>/);
  return m ? m[1].replace(/:$/, "").trim() : null;
}

// ════════════════════════════════════════════════════════════════════════════
// Research-report model — used by the redesigned Weekly page (WeeklyView + the
// app/components/weekly/* report components). Everything below is additive and
// pure; the legacy tab/panel helpers above are untouched.
// ════════════════════════════════════════════════════════════════════════════

// Strip HTML tags + decode entities to plain text, for number/keyword parsing.
const plain = (s) =>
  (s || "").replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/gi, " ")
           .replace(/[‐‑‒–—−]/g, "-").replace(/\s+/g, " ").trim();

// ISO-8601 week number + week-numbering year — for issue numbering
// ("Vol. 2026 · Issue 26"). dateStr is "YYYY-MM-DD".
export function isoWeek(dateStr) {
  if (!dateStr) return { year: null, week: null };
  const d = new Date(dateStr + "T00:00:00Z");
  const dayNr = (d.getUTCDay() + 6) % 7;          // Mon=0 … Sun=6
  d.setUTCDate(d.getUTCDate() - dayNr + 3);        // Thursday of this week
  const thu = d.getTime();
  d.setUTCMonth(0, 1);                             // Jan 1 of the ISO year
  if (d.getUTCDay() !== 4) {
    d.setUTCMonth(0, 1 + ((4 - d.getUTCDay() + 7) % 7));  // first Thursday
  }
  return { year: new Date(thu).getUTCFullYear(), week: 1 + Math.round((thu - d.getTime()) / 604800000) };
}

// Section taxonomy: maps a parsed emoji header to clean report metadata + the
// canonical report order (Market leads, Outlook closes).
const SECTION_DEFS = [
  { glyph: "📊", id: "market",     kind: "market",     nav: "Market"      },
  { glyph: "🏆", id: "movers",     kind: "movers",     nav: "Movers"      },
  { glyph: "🔗", id: "defi",       kind: "defi",       nav: "DeFi"        },
  { glyph: "🔥", id: "narratives", kind: "narratives", nav: "Narratives"  },
  { glyph: "📰", id: "stories",    kind: "stories",    nav: "Key Stories" },
  { glyph: "⚡", id: "outlook",    kind: "outlook",    nav: "Outlook"     },
];

// Decorate + order parsed sections into the report model. Each entry carries a
// 2-digit ordinal, anchor id, nav label, content-derived title, and its lines.
export function reportModel(sections) {
  const decorated = sections
    .map((s) => {
      const def = SECTION_DEFS.find((d) => s.hdr.startsWith(d.glyph));
      if (!def) return null;
      return { ...def, hdr: s.hdr, lines: s.lines, title: stripSectionEmoji(wDecode(s.hdr)) };
    })
    .filter(Boolean)
    .sort((a, b) => SECTION_DEFS.findIndex(d => d.id === a.id) - SECTION_DEFS.findIndex(d => d.id === b.id));
  return decorated.map((s, i) => ({ ...s, num: String(i + 1).padStart(2, "0") }));
}

// First sentence of the market prose → masthead standfirst (the week's thesis).
export function marketDek(marketLines) {
  const txt = plain((marketLines || []).join(" "));
  if (!txt || /data unavailable/i.test(txt)) return "";
  const m = txt.match(/^(.*?[.])\s/);
  return (m ? m[1] : txt).trim();
}

// Signed % near a ticker symbol in prose, e.g. "BTC (-3.6% 7d)" or "BTC +4.0%".
function pctNear(txt, sym) {
  const m = txt.match(new RegExp(`\\b${sym}\\b[^A-Za-z%]{0,16}?([+\\-]\\d+\\.?\\d*)\\s*%`, "i"));
  return m ? parseFloat(m[1]) : null;
}

// Parse the Market-section prose into a structured snapshot. Best-effort: only
// returns the cells it can extract with confidence (live weeks parse fully;
// historical-backfill weeks return {}). The structured `market_snapshot` column,
// when present, takes precedence over this (see snapshotCells).
export function parseMarketProse(marketLines) {
  const txt = plain((marketLines || []).join(" "));
  if (!txt || /data unavailable/i.test(txt)) return {};
  const out = {};

  let m = txt.match(/dominance[^.]*?\bto\s+([\d.]+)\s*%/i) || txt.match(/([\d.]+)\s*%\s*dominance/i);
  if (m) {
    out.btcDominance = { value: parseFloat(m[1]) };
    const pp = txt.match(/dominance[^.]*?\(?\s*([+\-][\d.]+)\s*pp/i);
    if (pp) out.btcDominance.delta = parseFloat(pp[1]);
  }

  const btc = pctNear(txt, "BTC");
  if (btc != null) out.btc7d = { value: btc };
  const eth = pctNear(txt, "ETH");
  if (eth != null) out.eth7d = { value: eth };

  m = txt.match(/(?:market cap|total cap|cap)[^$]{0,18}\$\s*([\d.]+)\s*([TBM])/i);
  if (m) {
    out.marketCap = { value: m[1], unit: m[2].toUpperCase() };  // keep raw string (preserve "2.20")
    const d = txt.match(/([+\-][\d.]+)\s*%\s*24h/i) || txt.match(/24h[^.]*?([+\-][\d.]+)\s*%/i);
    if (d) out.marketCap.delta = parseFloat(d[1]);
  }
  return out;
}

// Format a USD figure compactly: 2_200_000_000_000 → "$2.20T".
export function fmtUsdCompact(n) {
  if (n == null || !isFinite(n)) return null;
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9)  return `$${(n / 1e9).toFixed(1)}B`;
  if (abs >= 1e6)  return `$${(n / 1e6).toFixed(0)}M`;
  return `$${Math.round(n).toLocaleString("en-US")}`;   // whole-dollar prices (BTC/ETH), no decimals
}

// Build the ordered Market-Snapshot cells from the structured column (preferred)
// or parsed prose, plus the DeFi TVL reading the web reads from the DB. Each cell:
// { key, label, value, delta, deltaUnit, goodWhenUp }. Cells with no value are dropped.
export function snapshotCells({ snapshot, marketLines, defiTvl } = {}) {
  const s = snapshot && Object.keys(snapshot).length ? snapshot : null;
  const prose = s ? null : parseMarketProse(marketLines);
  const cells = [];

  // BTC / ETH spot — only the structured column carries price levels.
  if (s?.btc_price)
    cells.push({ key: "btc", label: "Bitcoin", value: fmtUsdCompact(s.btc_price),
                 delta: s.btc_7d_pct, deltaUnit: "7d", goodWhenUp: true });
  if (s?.eth_price)
    cells.push({ key: "eth", label: "Ethereum", value: fmtUsdCompact(s.eth_price),
                 delta: s.eth_7d_pct, deltaUnit: "7d", goodWhenUp: true });
  if (s?.eth_btc_ratio)
    cells.push({ key: "ethbtc", label: "ETH / BTC", value: s.eth_btc_ratio.toFixed(4), goodWhenUp: true });

  // Performance cells — from column or prose.
  const dom = s ? (s.btc_dominance != null ? { value: s.btc_dominance, delta: s.btc_dominance_delta_pp } : null)
                : prose.btcDominance;
  if (dom?.value != null)
    cells.push({ key: "dom", label: "Dominance", value: `${dom.value.toFixed(1)}%`,
                 delta: dom.delta, deltaUnit: "pp", goodWhenUp: null });

  const btc7 = s ? (s.btc_7d_pct != null && !s.btc_price ? { value: s.btc_7d_pct } : null) : prose.btc7d;
  if (btc7?.value != null && !cells.find(c => c.key === "btc"))
    cells.push({ key: "btc7", label: "BTC · 7d", value: signedPct(btc7.value), goodWhenUp: true, isDeltaValue: true });
  const eth7 = s ? (s.eth_7d_pct != null && !s.eth_price ? { value: s.eth_7d_pct } : null) : prose.eth7d;
  if (eth7?.value != null && !cells.find(c => c.key === "eth"))
    cells.push({ key: "eth7", label: "ETH · 7d", value: signedPct(eth7.value), goodWhenUp: true, isDeltaValue: true });

  const cap = s ? (s.total_market_cap_usd ? { value: s.total_market_cap_usd } : null) : null;
  if (cap?.value != null)
    cells.push({ key: "mcap", label: "Market Cap", value: fmtUsdCompact(cap.value),
                 delta: s.market_cap_change_24h_pct, deltaUnit: "24h", goodWhenUp: true });
  else if (prose.marketCap?.value != null)
    cells.push({ key: "mcap", label: "Market Cap",
                 value: `$${prose.marketCap.value}${prose.marketCap.unit}`,
                 delta: prose.marketCap.delta, deltaUnit: "24h", goodWhenUp: true });

  // DeFi TVL — always from the DB (zero-egress safe), if available.
  if (defiTvl?.tvl_now != null)
    cells.push({ key: "tvl", label: "DeFi TVL", value: fmtUsdCompact(defiTvl.tvl_now),
                 delta: defiTvl.pct, deltaUnit: "24h", goodWhenUp: true });

  return cells;
}

const signedPct = (n) => `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;

// Market-tone read for the masthead — rotation framing + a risk regime inferred
// from BTC's weekly move. Institutional, no glyph.
export function marketTone(rotation, marketLines, snapshot) {
  const label = { BTC: "BTC-Led", ETH: "ETH-Led", ALT: "Altcoin-Led", MIXED: "Mixed" }[rotation] || "Mixed";
  let btc = snapshot?.btc_7d_pct;
  if (btc == null) btc = parseMarketProse(marketLines).btc7d?.value;
  let regime = null;
  if (btc != null) regime = btc <= -2 ? "Risk-off" : btc >= 2 ? "Risk-on" : "Neutral";
  return { label, regime };
}

// Movers: structured { gainers:[{sym,pct}], losers:[…] }, each sorted by magnitude.
export function parseMovers(lines) {
  const out = { gainers: [], losers: [] };
  for (const ln of lines || []) {
    const toks = parseTokenPcts(ln);
    if (!toks.length) continue;
    const label = (getMoverLabel(ln) || "").toLowerCase();
    const bucket = label.includes("gain") ? out.gainers : label.includes("los") ? out.losers : null;
    if (bucket) bucket.push(...toks);
    else toks.forEach((t) => (t.pct >= 0 ? out.gainers : out.losers).push(t));
  }
  out.gainers.sort((a, b) => b.pct - a.pct);
  out.losers.sort((a, b) => a.pct - b.pct);
  return out;
}

// Best-effort one-line context for a mover: the first story/narrative line that
// mentions the ticker this week (so the table reads analytically, not as a list).
export function moverContext(sym, sections) {
  const re = new RegExp(`\\b${sym}\\b`);
  for (const id of ["stories", "narratives", "defi"]) {
    const sec = sections.find((s) => s.id === id);
    if (!sec) continue;
    for (const ln of sec.lines) {
      const p = plain(ln);
      if (re.test(p)) return p.replace(/\s*(link|↗)\s*$/i, "").trim();
    }
  }
  return null;
}

// Split a key-story line into { title, href } — the daily-brief click target +
// the original source link.
export function parseStoryLine(line) {
  const inner = line.startsWith("•") ? line.slice(1).trim() : line;
  const href = (inner.match(/<a[^>]*href="([^"]+)"/i) || [])[1] || null;
  const title = inner.replace(/<a[^>]*>.*?<\/a>/gi, "").replace(/\s*[–-]?\s*$/, "").trim();
  return { title, href };
}

// Re-export so callers can grab the range formatter from one place.
export { fmtWeekRange };
