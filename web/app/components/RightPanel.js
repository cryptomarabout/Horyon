"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import MomentumChip from "./MomentumChip";
import { stateMeta, typeMeta, timeAgo } from "../../lib/narratives";

// ── Formatters ─────────────────────────────────────────────────────────────
function fmtTvl(usd) {
  if (!usd && usd !== 0) return null;
  if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
  if (usd >= 1e9)  return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6)  return `$${(usd / 1e6).toFixed(0)}M`;
  return `$${usd.toLocaleString()}`;
}

function fmtPrice(usd) {
  if (usd == null) return null;
  if (usd >= 10000) return `$${(usd / 1000).toFixed(1)}K`;
  if (usd >= 1)     return `$${usd.toFixed(2)}`;
  if (usd >= 0.01)  return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(5)}`;
}

// ── External link icon ─────────────────────────────────────────────────────
function ExtIcon({ size = 8 }) {
  return (
    <svg
      width={size} height={size}
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0, display: "block" }}
    >
      <path d="M5 2H2a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V7" />
      <path d="M8 1h3v3" />
      <line x1="11" y1="1" x2="5.5" y2="6.5" />
    </svg>
  );
}

// ── X / Twitter icon ───────────────────────────────────────────────────────
function XIcon() {
  return (
    <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor"
      aria-hidden="true" style={{ flexShrink: 0, display: "block" }}>
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.742l7.737-8.835L1.254 2.25H8.08l4.259 5.626L18.243 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

// ── Chain distribution from chain_tvls JSONB ───────────────────────────────
function computeChainDist(chainTvls) {
  if (!chainTvls || typeof chainTvls !== "object") return [];
  const entries = Object.entries(chainTvls)
    .filter(([name]) => !name.includes("-") && !name.toLowerCase().includes("borrowed"))
    .map(([name, val]) => {
      const tvl = typeof val === "number" ? val
        : typeof val?.tvl === "number" ? val.tvl
        : 0;
      return { name, tvl };
    })
    .filter(e => e.tvl > 0)
    .sort((a, b) => b.tvl - a.tvl)
    .slice(0, 6);
  const total = entries.reduce((s, e) => s + e.tvl, 0);
  return entries.map(e => ({
    ...e,
    pct: total > 0 ? (e.tvl / total) * 100 : 0,
  }));
}

// ── Chain logo URL ─────────────────────────────────────────────────────────
const chainLogoUrl = name =>
  `https://icons.llamao.fi/icons/chains/rsz_${encodeURIComponent(name.toLowerCase())}.jpg`;

// ── AI skeleton loader ─────────────────────────────────────────────────────
function AiSkeleton() {
  return (
    <div className="panel-ai-skeleton">
      {[100, 92, 84, 55].map((w, i) => (
        <div key={i} className="panel-ai-bone" style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}

// ── Protocol card ──────────────────────────────────────────────────────────
function ProtocolCard({ p }) {
  const chg    = p.tvl_change_1d;
  const chgCls = chg == null ? "" : chg > 0 ? "up" : chg < 0 ? "dn" : "";
  const chainDist = computeChainDist(p.chain_tvls);
  const tvlFmt = fmtTvl(p.tvl_usd);
  const priceFmt = fmtPrice(p.price);

  return (
    <div className="panel-proto-card">
      <div className="panel-proto-top">
        {p.logo_url ? (
          <img
            src={p.logo_url}
            alt={p.name}
            className="panel-proto-logo"
            onError={e => { e.currentTarget.style.visibility = "hidden"; }}
          />
        ) : (
          <div className="panel-proto-logo" />
        )}

        <div className="panel-proto-info">
          <div className="panel-proto-name">{p.name}</div>
          {p.category && <div className="panel-proto-cat">{p.category}</div>}
          {priceFmt && (
            <div className="panel-proto-cat" style={{ color: "var(--accent-bright)", fontFamily: "var(--mono)", fontSize: "10px", marginTop: "2px" }}>
              {priceFmt}
            </div>
          )}
        </div>

        <div className="panel-proto-meta">
          {tvlFmt && <span className="panel-proto-tvl">{tvlFmt}</span>}
          {chg != null && (
            <span className={`panel-proto-chg ${chgCls}`}>
              {chg > 0 ? "▲" : chg < 0 ? "▼" : "–"}{Math.abs(chg).toFixed(1)}%
            </span>
          )}
          {p.url && (
            <a
              href={p.url}
              target="_blank"
              rel="noreferrer"
              className="panel-proto-link"
              onClick={e => e.stopPropagation()}
            >
              defillama.com ↗
            </a>
          )}
        </div>
      </div>

      {chainDist.length > 0 && (
        <div className="chain-dist-list">
          {chainDist.map(c => (
            <div key={c.name} className="chain-dist-row">
              <div className="chain-dist-info">
                <img
                  src={chainLogoUrl(c.name)}
                  alt={c.name}
                  className="chain-dist-logo"
                  onError={e => { e.currentTarget.style.visibility = "hidden"; }}
                />
                <span className="chain-dist-name">{c.name}</span>
                <span className="chain-dist-tvl">{fmtTvl(c.tvl)}</span>
                <span className="chain-dist-pct">{c.pct.toFixed(1)}%</span>
              </div>
              <div className="chain-dist-bar">
                <div className="chain-dist-fill" style={{ width: `${c.pct}%` }} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Chain card ─────────────────────────────────────────────────────────────
function ChainCard({ chain }) {
  return (
    <div className="panel-chain-card">
      <img
        src={chainLogoUrl(chain.name)}
        alt={chain.name}
        className="panel-chain-logo"
        onError={e => { e.currentTarget.style.visibility = "hidden"; }}
      />
      <div className="panel-chain-info">
        <div className="panel-chain-name">{chain.name}</div>
        {chain.rank && (
          <div className="panel-chain-rank">Rank #{chain.rank}</div>
        )}
      </div>
      {chain.tvl != null && fmtTvl(chain.tvl) && (
        <span className="panel-chain-tvl">{fmtTvl(chain.tvl)}</span>
      )}
    </div>
  );
}

// ── Weekly macro in the panel ──────────────────────────────────────────────

const W_ROT = {
  BTC:   { glyph: "₿", cls: "rot-btc",   label: "BTC Week",  badgeCls: "pw-badge-btc"  },
  ETH:   { glyph: "Ξ", cls: "rot-eth",   label: "ETH Week",  badgeCls: "pw-badge-eth"  },
  ALT:   { glyph: "◈", cls: "rot-alt",   label: "Alt Week",  badgeCls: "pw-badge-alt"  },
  MIXED: { glyph: "≋", cls: "rot-mixed", label: "Mixed Week", badgeCls: "pw-badge-mix" },
};
const W_ENT = { "&amp;":"&","&lt;":"<","&gt;":">","&quot;":'"',"&#39;":"'" };
const wDecode = s => s.replace(/&[a-z#0-9]+;/gi, m => W_ENT[m] ?? m);
const W_SEC_RE = /^<b>([📊🏆🔗🔥📰⚡][^<]*)<\/b>$/u;
const MO3 = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

// Tab definitions — Trending + Stories first, Movers last; id matches emoji prefix in section header
const PW_TABS = [
  { id: "all",  label: "All"      },
  { id: "🔥",  label: "Trending" },
  { id: "📰",  label: "Stories"  },
  { id: "📊",  label: "Market"   },
  { id: "🔗",  label: "DeFi"     },
  { id: "⚡",  label: "Watch"    },
  { id: "🏆",  label: "Movers"   },
];

// Sort order for "All" view — matches PW_TABS order (excluding "all")
const TAB_ORDER = PW_TABS.filter(t => t.id !== "all").map(t => t.id);

// Strip leading emoji from section header text (e.g. "📊 Market" → "Market")
const stripSectionEmoji = s => s.replace(/^[📊🏆🔗🔥📰⚡]\s*/u, "").trim();

function parseWeeklySections(html) {
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

// Colorize +/- percentages in HTML with green/red spans + triangles
function colorizePcts(html) {
  return html.replace(
    /([▲▼]?\s*[+\-]\d+\.?\d*%)/g,
    (m) => {
      const stripped = m.replace(/^[▲▼]\s*/, "").trim();
      const isUp = stripped.startsWith("+");
      const tri  = isUp ? "▲" : "▼";
      const cls  = isUp ? "pct-up" : "pct-dn";
      return `<span class="${cls}">${tri}&thinsp;${stripped}</span>`;
    }
  );
}

function stripTrailingPeriod(html) {
  // Remove a trailing period that appears just before the closing tag or end of string
  return html.replace(/\.\s*(<\/[^>]+>)?\s*$/, (_, tag) => tag || "");
}

function WeeklyLine({ line, colorize }) {
  const isBullet = line.startsWith("•");
  const raw      = isBullet ? line.slice(1).trim() : line;
  const html     = stripTrailingPeriod(colorize ? colorizePcts(raw) : raw);
  if (isBullet) {
    return (
      <div className="pw-item">
        <span className="pw-dot" aria-hidden="true" />
        <span dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    );
  }
  return <p className="pw-para" dangerouslySetInnerHTML={{ __html: html }} />;
}

// ── Movers: parse token-pct pairs and render as chips ────────────────────
function parseTokenPcts(html) {
  // Normalize Unicode minus variants (U+2011 non-breaking hyphen, U+2212 minus sign, etc.)
  // to ASCII '-' so the regex reliably matches LLM output for losers
  const text = html
    .replace(/<[^>]*>/g, "")
    .replace(/&[a-z#0-9]+;/gi, " ")
    .replace(/[‐‑‒–—−∕]/g, "-");
  const re = /\b([A-Z]{2,10})\s+([+\-]\d+\.?\d*)%/g;
  const pairs = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    pairs.push({ sym: m[1], pct: parseFloat(m[2]) });
  }
  return pairs;
}

function getMoverLabel(html) {
  const m = html.match(/<b>([^<]+?):?\s*<\/b>/);
  return m ? m[1].replace(/:$/, "").trim() : null;
}

function TokenChip({ sym, pct }) {
  const isUp = pct > 0;
  return (
    <div className={`mover-chip mover-chip--${isUp ? "up" : "dn"}`}>
      <span className="mover-chip-sym">{sym}</span>
      <span className="mover-chip-pct">
        {isUp ? "▲" : "▼"}&thinsp;{isUp ? "+" : ""}{pct.toFixed(1)}%
      </span>
    </div>
  );
}

function MoversLine({ line }) {
  const isBullet = line.startsWith("•");
  const inner = isBullet ? line.slice(1).trim() : line;
  const tokens = parseTokenPcts(inner);

  if (tokens.length === 0) {
    return <WeeklyLine line={line} colorize />;
  }

  const label = getMoverLabel(inner);
  const isUp  = tokens[0]?.pct > 0;
  return (
    <div className="mover-group">
      {label && (
        <span className={`mover-label ${isUp ? "mover-label--gain" : "mover-label--loss"}`}>
          {label}
        </span>
      )}
      <div className="mover-chips">
        {tokens.map(t => <TokenChip key={t.sym} sym={t.sym} pct={t.pct} />)}
      </div>
    </div>
  );
}

// ── Key Stories: navigate to the matching daily digest page ──────────────
function KeyStoryLine({ line, weekStart, weekEnd, onOpenArticle }) {
  const isBullet = line.startsWith("•");
  const inner    = isBullet ? line.slice(1).trim() : line;

  // Strip the external link from display
  const displayHtml = inner.replace(/<a[^>]*>.*?<\/a>/g, "").replace(/\s*–?\s*$/, "").trim();

  // Extract plain title for the API lookup
  const plainTitle = displayHtml.replace(/<[^>]*>/g, "").replace(/&[a-z#0-9]+;/gi, " ").trim();

  const handleClick = useCallback(async (e) => {
    e.preventDefault();
    try {
      const r = await fetch("/api/find-digest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: plainTitle, week_start: weekStart, week_end: weekEnd }),
      });
      const data = await r.json();
      if (data.date) {
        onOpenArticle({ date: data.date, title: plainTitle, body: data.body || null, link: data.link || null });
      }
    } catch { /* ignore */ }
  }, [plainTitle, weekStart, weekEnd, onOpenArticle]);

  if (!isBullet) return <p className="pw-para" dangerouslySetInnerHTML={{ __html: displayHtml }} />;

  return (
    <div className="pw-item pw-item--ks" onClick={handleClick} role="button" tabIndex={0}
      onKeyDown={e => e.key === "Enter" && handleClick(e)}>
      <span className="pw-dot" aria-hidden="true" />
      <span dangerouslySetInnerHTML={{ __html: displayHtml }} />
      <span className="ks-arrow" aria-hidden="true">→</span>
    </div>
  );
}

function WeeklyPanel({ weekly, onClose, onOpenArticle }) {
  const [activeTab, setActiveTab] = useState("all");
  const rot      = W_ROT[weekly.rotation] || W_ROT.MIXED;
  const sections = parseWeeklySections(weekly.content);
  const [,ms,ds] = (weekly.week_start||"").split("-").map(Number);
  const [,me,de] = (weekly.week_end  ||"").split("-").map(Number);
  const range = ms === me
    ? `${MO3[ms-1]} ${ds}–${de}`
    : `${MO3[ms-1]} ${ds}–${MO3[me-1]} ${de}`;

  const visible = activeTab === "all"
    ? [...sections].sort((a, b) => {
        const ai = TAB_ORDER.findIndex(id => a.hdr.startsWith(id));
        const bi = TAB_ORDER.findIndex(id => b.hdr.startsWith(id));
        return (ai < 0 ? TAB_ORDER.length : ai) - (bi < 0 ? TAB_ORDER.length : bi);
      })
    : sections.filter(s => s.hdr.startsWith(activeTab));

  const isMovers    = (s) => s.hdr.startsWith("🏆");
  const isKeyStory  = (s) => s.hdr.startsWith("📰");
  const isMarketSec = (s) => s.hdr.startsWith("📊");

  return (
    <>
      {/* Sticky header */}
      <div className="panel-header panel-header--tabbed">
        <div className="panel-title-row">
          <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "baseline", gap: "7px" }}>
            <span className="pw-eyebrow">Weekly Macro</span>
            <span className="pw-range">{range}</span>
          </div>
          <button className="panel-close" onClick={onClose} aria-label="Close panel">✕</button>
        </div>

        {/* Section tabs */}
        <div className="pw-tabs" role="tablist" aria-label="Weekly sections">
          {PW_TABS.map(tab => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`pw-tab${activeTab === tab.id ? " active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Scrollable content */}
      <div className="panel-scroll">
        <div className="panel-body">
          {visible.length === 0 ? (
            <p className="pw-empty">No content for this section.</p>
          ) : visible.map((s, i) => (
            <div key={i} className="pw-section">
              <div className="pw-section-hdr">
                {stripSectionEmoji(wDecode(s.hdr))}
                {isMarketSec(s) && (
                  <span className={`pw-badge pw-badge--sm ${rot.badgeCls}`}>
                    <span className="pw-badge-glyph">{rot.glyph}</span>{rot.label}
                  </span>
                )}
              </div>
              <div className="pw-lines">
                {s.lines.map((ln, j) => (
                  isMovers(s)
                    ? <MoversLine key={j} line={ln} />
                    : isKeyStory(s)
                      ? <KeyStoryLine key={j} line={ln}
                          weekStart={weekly.week_start} weekEnd={weekly.week_end}
                          onOpenArticle={onOpenArticle} />
                      : <WeeklyLine key={j} line={ln} colorize={false} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

// ── Search icon (small, for panel header) ──────────────────────────────────
function SearchIconSm() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0 }}>
      <circle cx="6.5" cy="6.5" r="4.5" />
      <line x1="10.5" y1="10.5" x2="14" y2="14" />
    </svg>
  );
}

// ── Parse Telegram-format output into typed lines ─────────────────────────
function parseTelegramLines(html) {
  return (html || "")
    .split("\n")
    .map(l => l.trim())
    .filter(Boolean)
    .map(line => {
      if (line.startsWith("•")) {
        // Add target="_blank" to any <a> tags so links open in new tab
        const body = line.slice(1).trim().replace(
          /<a\s+href=/g,
          '<a target="_blank" rel="noreferrer" href='
        );
        return { type: "bullet", html: body };
      }
      return { type: "header", html: line };
    });
}

// ── Search panel ───────────────────────────────────────────────────────────
function SearchPanel({ search }) {
  const { keyword, state, result, sources, synth, synthState, onClose } = search;
  const lines = parseTelegramLines(result);
  const bullets = lines.filter(l => l.type === "bullet");
  const synthLines   = parseTelegramLines(synth || "");
  const synthBullets = synthLines.filter(l => l.type === "bullet");
  const hasSynthSection = synthState === "loading" || synthBullets.length > 0;

  return (
    <>
      {/* Sticky header */}
      <div className="panel-header">
        <div className="panel-title-row">
          <div style={{ display: "flex", alignItems: "center", gap: "7px", flex: 1, minWidth: 0 }}>
            <span style={{ color: "var(--text-3)", display: "flex", flexShrink: 0 }}>
              <SearchIconSm />
            </span>
            <span className="search-kw-badge">{keyword}</span>
          </div>
          <button className="panel-close" onClick={onClose} aria-label="Close search">✕</button>
        </div>
        <div className="search-panel-eyebrow">Intel Brief</div>
      </div>

      {/* Scrollable body */}
      <div className="panel-scroll">

        {/* ── Loading ── */}
        {state === "loading" && (
          <div className="search-loading" aria-live="polite" aria-busy="true">
            <div className="search-loading-ring" aria-hidden="true" />
            <div className="search-loading-text">
              <p className="search-loading-label">Analyzing latest news on</p>
              <span className="search-loading-kw">"{keyword}"</span>
            </div>
          </div>
        )}

        {/* ── Result ── */}
        {state === "done" && (
          <div className="panel-body" style={{ padding: "0" }}>
            {/* Source count row */}
            {sources > 0 && (
              <div className="search-meta-row" style={{ padding: "10px 14px" }}>
                <svg width="10" height="10" viewBox="0 0 12 12" fill="none"
                  stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
                  aria-hidden="true" style={{ flexShrink: 0, color: "var(--text-4)" }}>
                  <circle cx="6" cy="6" r="4.5" />
                  <line x1="6" y1="4" x2="6" y2="6.5" />
                  <circle cx="6" cy="8.5" r="0.4" fill="currentColor" stroke="none" />
                </svg>
                <span className="search-src-count">
                  {bullets.length} updates · last 30 days
                </span>
              </div>
            )}

            {/* Analyst synthesis (entity click) — streams in above the feed */}
            {synthState === "loading" && (
              <div className="search-synth-loading" aria-live="polite" aria-busy="true">
                <span className="search-synth-spinner" aria-hidden="true" />
                Synthesizing analyst view…
              </div>
            )}
            {synthBullets.length > 0 && (
              <div className="search-synth">
                <div className="panel-section-label">Analyst View</div>
                <div className="search-bullet-list">
                  {synthLines.map((item, i) =>
                    item.type === "bullet" ? (
                      <div
                        key={`s${i}`}
                        className="search-bullet"
                        style={{ "--i": synthBullets.indexOf(item) }}
                      >
                        <span className="search-bullet-dot" aria-hidden="true">•</span>
                        <span
                          className="search-bullet-body"
                          dangerouslySetInnerHTML={{ __html: item.html }}
                        />
                      </div>
                    ) : null
                  )}
                </div>
              </div>
            )}

            {hasSynthSection && (
              <div className="panel-section-label" style={{ marginTop: "4px" }}>Recent mentions</div>
            )}

            {/* Bullet list */}
            <div className="search-bullet-list">
              {lines.map((item, i) =>
                item.type === "bullet" ? (
                  <div
                    key={i}
                    className="search-bullet"
                    style={{ "--i": bullets.indexOf(item) }}
                  >
                    <span className="search-bullet-dot" aria-hidden="true">•</span>
                    <span
                      className="search-bullet-body"
                      dangerouslySetInnerHTML={{ __html: item.html }}
                    />
                  </div>
                ) : null
              )}
            </div>
          </div>
        )}

        {/* ── Error ── */}
        {state === "error" && (
          <div className="panel-body">
            <p className="search-error">{result}</p>
          </div>
        )}

      </div>
    </>
  );
}

// ── Podcast intel panel ─────────────────────────────────────────────────────
const POD_SENT = {
  bullish: { label: "Bullish", cls: "pod-badge--bull", glyph: "▲" },
  bearish: { label: "Bearish", cls: "pod-badge--bear", glyph: "▼" },
  neutral: { label: "Neutral", cls: "pod-badge--neut", glyph: "–" },
  mixed:   { label: "Mixed",   cls: "pod-badge--mix",  glyph: "◇" },
};

function fmtPodFull(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${MO3[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

function PodcastBulletList({ title, items }) {
  if (!items?.length) return null;
  return (
    <div>
      <div className="panel-section-label">{title}</div>
      <div className="pw-lines">
        {items.map((t, i) => (
          <div key={i} className="pw-item">
            <span className="pw-dot" aria-hidden="true" />
            <span>{t}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PodcastChips({ title, items }) {
  if (!items?.length) return null;
  return (
    <div>
      <div className="panel-section-label">{title}</div>
      <div className="pod-chips">
        {items.map((t, i) => <span key={i} className="pod-chip">{t}</span>)}
      </div>
    </div>
  );
}

function PodcastPanel({ podcast, onClose }) {
  const a = podcast.analysis || {};
  const sent = POD_SENT[a.sentiment] || POD_SENT.mixed;
  return (
    <>
      <div className="panel-header">
        <div className="panel-title-row">
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "3px" }}>
            <span className="pw-eyebrow">Podcast Intel · {podcast.channel}</span>
            <h2 className="panel-title panel-title--pod">{podcast.title}</h2>
          </div>
          <button className="panel-close" onClick={onClose} aria-label="Close panel">✕</button>
        </div>
        <div className="pod-meta-row">
          <span className={`pod-badge ${sent.cls}`}>
            <span aria-hidden="true">{sent.glyph}</span> {sent.label}
          </span>
          <span className="pod-date">{fmtPodFull(podcast.published_at)}</span>
          {podcast.url && (
            <a href={podcast.url} target="_blank" rel="noreferrer"
              className="panel-src-link" onClick={e => e.stopPropagation()}>
              <span>YouTube</span>
              <ExtIcon size={9} />
            </a>
          )}
        </div>
      </div>

      <div className="panel-scroll">
        <div className="panel-body">
          {a.tldr && (
            <div>
              <div className="panel-section-label">Summary</div>
              <p className="panel-ai-text">{a.tldr}</p>
            </div>
          )}
          <PodcastBulletList title="Key Claims" items={a.notable_claims} />
          <PodcastBulletList title="Predictions" items={a.predictions} />
          <PodcastChips title="Themes" items={a.themes} />
          <PodcastChips title="Entities" items={a.entities} />
          {a.guests?.length > 0 && (
            <div>
              <div className="panel-section-label">Guests</div>
              <p className="panel-ai-text">{a.guests.join(", ")}</p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ── Narrative panel ──────────────────────────────────────────────────────────
function NarrativeEvidenceRow({ s }) {
  const tm = typeMeta(s.signal_type);
  const ago = timeAgo(s.ts);
  const Tag = s.url ? "a" : "div";
  const props = s.url
    ? { href: s.url, target: "_blank", rel: "noreferrer" }
    : {};
  return (
    <Tag className={`narr-ev narr-ev--${s.signal_type}`} {...props}>
      <span className="narr-ev-glyph" aria-hidden>{tm.glyph}</span>
      <span className="narr-ev-body">
        <span className="narr-ev-title">{s.title}</span>
        <span className="narr-ev-meta">
          {ago && <span>{ago}</span>}
          {s.source_count >= 2 && <span>· {s.source_count} src</span>}
          {s.importance != null && <span>· {s.importance}</span>}
        </span>
      </span>
    </Tag>
  );
}

function NarrativePanel({ narrative, onClose }) {
  const sm = stateMeta(narrative.state);
  const signals = narrative.signals || [];
  const entities = narrative.entities || [];
  return (
    <>
      <div className="panel-header">
        <div className="panel-title-row">
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "4px" }}>
            <span className="pw-eyebrow narr-eyebrow">
              <span className={`narr-eyebrow-glyph narr-side-glyph--${sm.cls}`} aria-hidden>{sm.glyph}</span>
              Narrative · {sm.label}
            </span>
            <h2 className="panel-title">{narrative.label}</h2>
          </div>
          <button className="panel-close" onClick={onClose} aria-label="Close panel">✕</button>
        </div>
        <div className="narr-meta-row">
          <MomentumChip rho={narrative.momentum_ratio} delta={narrative.delta_48h} state={narrative.state} expanded />
          <span className="narr-meta-n">{narrative.signal_count} signals</span>
        </div>
      </div>

      <div className="panel-scroll">
        <div className="panel-body">
          {narrative.thesis && (
            <div>
              <div className="panel-section-label">Thesis</div>
              <p className="panel-ai-text">{narrative.thesis}</p>
            </div>
          )}

          {entities.length > 0 && (
            <div>
              <div className="panel-section-label">Key Entities</div>
              <div className="pod-chips">
                {entities.slice(0, 8).map(e => (
                  <span key={e.slug} className="pod-chip narr-ent-chip">
                    {e.logo_url && (
                      <img src={e.logo_url} alt="" className="narr-ent-logo"
                        onError={ev => { ev.currentTarget.style.display = "none"; }} />
                    )}
                    {e.name}
                  </span>
                ))}
              </div>
            </div>
          )}

          {signals.length > 0 && (
            <div>
              <div className="panel-section-label">Evidence · {signals.length}</div>
              <div className="narr-ev-list">
                {signals.map((s, i) => <NarrativeEvidenceRow key={`${s.signal_ref}:${i}`} s={s} />)}
              </div>
            </div>
          )}

          {narrative.watch_next?.length > 0 && (
            <div>
              <div className="panel-section-label">Watch Next</div>
              <div className="pw-lines">
                {narrative.watch_next.map((w, i) => (
                  <div key={i} className="pw-item">
                    <span className="pw-dot" aria-hidden="true" />
                    <span>{w}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {narrative.contrarian && (
            <div>
              <div className="panel-section-label">Contrarian</div>
              <p className="panel-ai-text narr-contra">{narrative.contrarian}</p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────
function EmptyState() {
  return (
    <div className="panel-empty">
      <div className="panel-empty-glyph">◈</div>
      <p className="panel-empty-label">
        Select a story to view<br />project data &amp; analysis
      </p>
    </div>
  );
}

// ── Related article row ─────────────────────────────────────────────────────
const MO_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function fmtRelDate(iso) {
  const [, m, d] = (iso || "").split("-").map(Number);
  return `${MO_SHORT[m - 1] ?? "?"} ${d}`;
}

function getDomain(url) {
  if (!url) return null;
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return null; }
}

function RelatedArticle({ article, onOpen }) {
  return (
    <div
      className="related-item related-item--clickable"
      onClick={() => onOpen(article)}
      role="button"
      tabIndex={0}
      onKeyDown={e => e.key === "Enter" && onOpen(article)}
    >
      <div className="related-item-head">
        <span className="related-date">{fmtRelDate(article.date)}</span>
        <span className="related-title">{article.title}</span>
      </div>
      {article.body && (
        <p className="related-body">{article.body}</p>
      )}
    </div>
  );
}

// ── Related article detail layer ────────────────────────────────────────────
function RelatedDetailLayer({ article, onBack, onClose }) {
  const domain = getDomain(article.link);
  return (
    <>
      <div className="panel-header">
        <div className="panel-title-row">
          <button className="panel-back-btn" onClick={onBack} aria-label="Back to article">
            ← Back
          </button>
          <button className="panel-close" onClick={onClose} aria-label="Close panel">✕</button>
        </div>
        {article.link && (
          <a href={article.link} target="_blank" rel="noreferrer" className="panel-src-link">
            <span>{domain ?? "Source"}</span>
            <ExtIcon size={9} />
          </a>
        )}
      </div>
      <div className="panel-scroll">
        <div className="panel-body">
          <div className="related-layer-date">{fmtRelDate(article.date)}</div>
          <h3 className="related-layer-title">{article.title}</h3>
          {article.body
            ? <p className="related-layer-body">{article.body}</p>
            : <p style={{ fontSize: "11px", color: "var(--text-4)" }}>No additional content.</p>
          }
        </div>
      </div>
    </>
  );
}

// ── Main panel ─────────────────────────────────────────────────────────────
export default function RightPanel({ bullet, hint, cachedAnalysis, onClose, weekly, podcast, narrative, search }) {
  const [aiState, setAiState] = useState("idle"); // idle | loading | done | error
  const [aiText, setAiText]   = useState("");
  const [related,        setRelated]        = useState([]);
  const [relatedLoading, setRelatedLoading] = useState(false);
  const [relatedView,    setRelatedView]    = useState(null);
  const prevTitle = useRef(null);

  // AI analysis
  useEffect(() => {
    if (!bullet) { setAiState("idle"); setAiText(""); return; }
    if (bullet.title === prevTitle.current) return;
    prevTitle.current = bullet.title;

    if (cachedAnalysis) {
      setAiText(cachedAnalysis);
      setAiState("done");
      return;
    }

    setAiState("loading");
    setAiText("");

    const ctrl = new AbortController();
    fetch("/api/details", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: bullet.title, body: bullet.body }),
      signal: ctrl.signal,
    })
      .then(r => r.json())
      .then(data => {
        setAiText(data.content || "No additional details.");
        setAiState("done");
      })
      .catch(err => {
        if (err.name === "AbortError") return;
        setAiText("Could not load analysis.");
        setAiState("error");
      });

    return () => ctrl.abort();
  }, [bullet?.title, cachedAnalysis]);

  // Reset related overlay when main content context changes
  useEffect(() => { setRelatedView(null); }, [bullet?.title, weekly?.week_start, podcast?.video_id, narrative?.slug, search?.keyword]);

  // Related articles — fires in parallel with AI analysis
  useEffect(() => {
    if (!bullet) { setRelated([]); setRelatedLoading(false); return; }

    const protocols = (hint?.protocols || []).map(p => p.name);
    const chains    = (hint?.chains    || []).map(c => c.name);

    setRelated([]);
    setRelatedLoading(true);

    const ctrl = new AbortController();
    fetch("/api/related", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ protocols, chains, title: bullet.title }),
      signal: ctrl.signal,
    })
      .then(r => r.json())
      .then(data => { setRelated(data.articles || []); setRelatedLoading(false); })
      .catch(err => { if (err.name !== "AbortError") setRelatedLoading(false); });

    return () => ctrl.abort();
  }, [bullet?.title, hint]);

  const { protocols = [], chains = [] } = hint || {};
  const hasProjects = protocols.length > 0 || chains.length > 0;
  const srcIsTwitter = bullet?.src?.type === "twitter";

  let mainContent;
  if (!bullet) {
    if (search?.keyword)      mainContent = <SearchPanel search={search} />;
    else if (narrative)       mainContent = <NarrativePanel narrative={narrative} onClose={onClose} />;
    else if (podcast)         mainContent = <PodcastPanel podcast={podcast} onClose={onClose} />;
    else if (weekly?.content) mainContent = <WeeklyPanel weekly={weekly} onClose={onClose} onOpenArticle={setRelatedView} />;
    else                      mainContent = <EmptyState />;
  } else {
    mainContent = (
      <>
        <div className="panel-header">
          <div className="panel-title-row">
            <h2 className="panel-title">
              {bullet.title}
            </h2>
            <button className="panel-close" onClick={onClose} aria-label="Close panel">
              ✕
            </button>
          </div>

          {bullet.link && (
            <a
              href={bullet.link}
              target="_blank"
              rel="noreferrer"
              className="panel-src-link"
              onClick={e => e.stopPropagation()}
            >
              {srcIsTwitter ? <XIcon /> : null}
              <span>{bullet.src?.name ?? "Source"}</span>
              <ExtIcon size={9} />
            </a>
          )}
        </div>

        <div className="panel-scroll">
          <div className="panel-body">

            <div>
              <div className="panel-section-label">Analyst View</div>
              {aiState === "loading" && <AiSkeleton />}
              {aiState !== "loading" && (
                <p className={`panel-ai-text${aiState === "error" ? " error" : ""}`}>
                  {aiText}
                </p>
              )}
            </div>

            {protocols.length > 0 && (
              <div>
                <div className="panel-section-label">DeFiLlama · Protocol TVL</div>
                {protocols.map(p => (
                  <ProtocolCard key={p.name} p={p} />
                ))}
              </div>
            )}

            {chains.length > 0 && (
              <div>
                <div className="panel-section-label">Chain TVL</div>
                {chains.map(c => (
                  <ChainCard key={c.name} chain={c} />
                ))}
              </div>
            )}

            {!hasProjects && aiState !== "loading" && (
              <p style={{ fontSize: "11px", color: "var(--text-4)", lineHeight: 1.5 }}>
                No entity found on DeFiLlama for this news.
              </p>
            )}

            {(relatedLoading || related.length > 0) && (
              <div>
                <div className="panel-section-label">Related Stories</div>
                {relatedLoading && related.length === 0 && (
                  <div className="related-skeleton">
                    {[85, 72, 90].map((w, i) => (
                      <div key={i} className="related-bone" style={{ width: `${w}%` }} />
                    ))}
                  </div>
                )}
                <div className="related-list">
                  {related.map((a, i) => (
                    <RelatedArticle key={i} article={a} onOpen={setRelatedView} />
                  ))}
                </div>
              </div>
            )}

          </div>
        </div>
      </>
    );
  }

  // Key drives a re-mount (and CSS animation) whenever the panel content context changes
  const animKey = bullet?.title ?? search?.keyword ?? narrative?.slug ?? podcast?.video_id ?? weekly?.week_start ?? "empty";

  return (
    <div className="panel-container">
      <div key={animKey} className="panel-anim">
        {mainContent}
      </div>
      {relatedView && (
        <div className="panel-layer">
          <RelatedDetailLayer
            article={relatedView}
            onBack={() => setRelatedView(null)}
            onClose={onClose}
          />
        </div>
      )}
    </div>
  );
}
