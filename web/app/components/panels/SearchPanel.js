"use client";

import { SearchIcon } from "../icons";
import PanelSection from "../ui/PanelSection";
import PanelHeader from "../ui/PanelHeader";
import EmptyState from "../ui/EmptyState";
import { fmtDayAgo, fmtTvl, fmtPrice, fmtPct } from "../../../lib/format";

// Structured price/mcap/TVL stat strip shown above a resolved intel brief — the same
// visual language as the Atlas node panel's Fundamentals block (reuses its `eg-funda`
// classes, loaded globally). Analyst-grade numbers belong in a scannable strip, not
// buried in brief prose alone. Renders nothing if the entity has neither TVL nor
// market data (the common case for non-token entities).
const flowDir = (v) => (v > 0 ? "up" : v < 0 ? "dn" : "flat");

function FactsStrip({ facts }) {
  if (!facts) return null;
  const price = fmtPrice(facts.price);
  const mcap = fmtTvl(facts.marketCap);
  const tvl = fmtTvl(facts.tvl);
  if (!price && !mcap && !tvl) return null;
  const fdv = fmtTvl(facts.fdv);
  const chg7 = fmtPct(facts.priceChange7d);
  const tvlChg7 = fmtPct(facts.tvlChange7d);
  const stats = [
    price && { k: "Price", v: price },
    mcap && { k: "Mcap", v: mcap },
    chg7 != null && { k: "Price 7d", v: chg7, cls: `pl-flow-val ${flowDir(facts.priceChange7d)}` },
    fdv && { k: "FDV", v: fdv },
    tvl && { k: "TVL", v: tvl },
    tvlChg7 != null && { k: "TVL 7d", v: tvlChg7, cls: `pl-flow-val ${flowDir(facts.tvlChange7d)}` },
    facts.tokenSymbol && { k: "Token", v: facts.tokenSymbol },
  ].filter(Boolean);
  return (
    <div style={{ padding: "12px 14px 0" }}>
      <div className="eg-funda">
        {stats.map((s) => (
          <div className="eg-funda-stat" key={s.k}>
            <span className="eg-funda-k">{s.k}</span>
            <span className={`eg-funda-v ${s.cls || ""}`}>{s.v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Parse Telegram-format output (bullet lines vs header lines).
//
// Entity briefs are precomputed by an LLM and occasionally drift from the exact
// "🔎 header \n\n • bullet" template it's instructed to emit (server-side
// normalization in app/entity_brief.py fixes this at write time — see
// _normalize_brief_format — but the multi-model fallback chain means a new
// provider can reintroduce a drift pattern we haven't normalized yet). Rather than
// silently dropping an unrecognized line, this parser stays lenient: it also
// accepts a leading "-" as a bullet marker, and splits a header line that has a
// bullet merged onto it via an inline " • " separator.
function linkify(body) {
  return body.replace(/<a\s+href=/g, '<a target="_blank" rel="noreferrer" href=');
}

function parseTelegramLines(html) {
  const out = [];
  for (const raw of (html || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("•")) {
      out.push({ type: "bullet", html: linkify(line.slice(1).trim()) });
    } else if (/^-\s+/.test(line)) {
      out.push({ type: "bullet", html: linkify(line.replace(/^-\s+/, "")) });
    } else if (line.startsWith("🔎") && line.includes(" • ")) {
      const idx = line.indexOf(" • ");
      out.push({ type: "header", html: line.slice(0, idx) });
      out.push({ type: "bullet", html: linkify(line.slice(idx + 3).trim()) });
    } else {
      out.push({ type: "header", html: line });
    }
  }
  return out;
}

export default function SearchPanel({ search }) {
  const { keyword, state, result, sources, synth, synthState, asOf, facts, onClose } = search;
  const lines        = parseTelegramLines(result);
  const bullets      = lines.filter(l => l.type === "bullet");
  const synthLines   = parseTelegramLines(synth || "");
  const synthBullets = synthLines.filter(l => l.type === "bullet");
  const hasSynthSection = synthState === "loading" || synthBullets.length > 0;
  // Belt-and-suspenders: if parsing ever finds zero renderable bullets on a "done"
  // result (a future model-format drift the parser doesn't yet recognize, or a
  // genuinely empty brief), show an explicit message instead of a silent blank panel —
  // exactly the failure mode that made the earlier formatting bug look like nothing
  // was wrong. See parseTelegramLines above for the drift patterns already handled.
  const isEmpty = state === "done" && synthState !== "loading"
    && bullets.length === 0 && synthBullets.length === 0;

  return (
    <>
      <PanelHeader
        onClose={onClose}
        closeLabel="Close search"
        below={<div className="search-panel-eyebrow">Intel Brief</div>}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "7px", flex: 1, minWidth: 0 }}>
          <span style={{ color: "var(--text-3)", display: "flex", flexShrink: 0 }}>
            <SearchIcon size={11} />
          </span>
          <span className="search-kw-badge">{keyword}</span>
        </div>
      </PanelHeader>

      <div className="panel-scroll">
        {state === "loading" && (
          <div className="search-loading" aria-live="polite" aria-busy="true">
            <div className="search-loading-ring" aria-hidden="true" />
            <div className="search-loading-text">
              <p className="search-loading-label">Analyzing latest news on</p>
              <span className="search-loading-kw">"{keyword}"</span>
            </div>
          </div>
        )}

        {state === "done" && (
          <div className="panel-body" style={{ padding: "0" }}>
            <FactsStrip facts={facts} />
            {isEmpty ? (
              <EmptyState glyph="◈">No structured update for "{keyword}" right now.</EmptyState>
            ) : asOf ? (
              <div className="search-meta-row" style={{ padding: "10px 14px" }}>
                <svg width="10" height="10" viewBox="0 0 12 12" fill="none"
                  stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
                  aria-hidden="true" style={{ flexShrink: 0, color: "var(--text-4)" }}>
                  <circle cx="6" cy="6" r="4.5" />
                  <line x1="6" y1="4" x2="6" y2="6.5" />
                  <circle cx="6" cy="8.5" r="0.4" fill="currentColor" stroke="none" />
                </svg>
                <span className="search-src-count">
                  Intel brief · as of {fmtDayAgo(asOf)}
                </span>
              </div>
            ) : sources > 0 && (
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

            {synthState === "loading" && (
              <div className="search-synth-loading" aria-live="polite" aria-busy="true">
                <span className="search-synth-spinner" aria-hidden="true" />
                Synthesizing analyst view…
              </div>
            )}
            {synthBullets.length > 0 && (
              <PanelSection label="Analyst View" className="search-synth">
                <div className="search-bullet-list">
                  {synthLines.map((item, i) =>
                    item.type === "bullet" ? (
                      <div key={`s${i}`} className="search-bullet"
                        style={{ "--i": synthBullets.indexOf(item) }}>
                        <span className="search-bullet-dot" aria-hidden="true">•</span>
                        <span className="search-bullet-body"
                          dangerouslySetInnerHTML={{ __html: item.html }} />
                      </div>
                    ) : null
                  )}
                </div>
              </PanelSection>
            )}

            {hasSynthSection && (
              <div className="panel-section-label" style={{ marginTop: "4px" }}>Recent mentions</div>
            )}

            <div className="search-bullet-list">
              {lines.map((item, i) =>
                item.type === "bullet" ? (
                  <div key={i} className="search-bullet"
                    style={{ "--i": bullets.indexOf(item) }}>
                    <span className="search-bullet-dot" aria-hidden="true">•</span>
                    <span className="search-bullet-body"
                      dangerouslySetInnerHTML={{ __html: item.html }} />
                  </div>
                ) : null
              )}
            </div>
          </div>
        )}

        {state === "error" && (
          <div className="panel-body">
            <p className="search-error">{result}</p>
          </div>
        )}
      </div>
    </>
  );
}
