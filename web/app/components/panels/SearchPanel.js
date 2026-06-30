"use client";

import { SearchIcon } from "../icons";
import PanelSection from "../ui/PanelSection";
import PanelHeader from "../ui/PanelHeader";

// Parse Telegram-format output (bullet lines vs header lines).
function parseTelegramLines(html) {
  return (html || "")
    .split("\n")
    .map(l => l.trim())
    .filter(Boolean)
    .map(line => {
      if (line.startsWith("•")) {
        const body = line.slice(1).trim().replace(
          /<a\s+href=/g,
          '<a target="_blank" rel="noreferrer" href='
        );
        return { type: "bullet", html: body };
      }
      return { type: "header", html: line };
    });
}

export default function SearchPanel({ search }) {
  const { keyword, state, result, sources, synth, synthState, onClose } = search;
  const lines        = parseTelegramLines(result);
  const bullets      = lines.filter(l => l.type === "bullet");
  const synthLines   = parseTelegramLines(synth || "");
  const synthBullets = synthLines.filter(l => l.type === "bullet");
  const hasSynthSection = synthState === "loading" || synthBullets.length > 0;

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
