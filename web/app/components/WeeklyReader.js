"use client";

import { useCallback } from "react";
import {
  rotMeta, wDecode, stripSectionEmoji, sectionTone,
  colorizePcts, stripTrailingPeriod, parseTokenPcts, getMoverLabel,
} from "../../lib/weekly";

// ── Section line renderers (shared by the page reader + legacy panel) ────────
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
  const inner    = isBullet ? line.slice(1).trim() : line;
  const tokens   = parseTokenPcts(inner);
  if (tokens.length === 0) return <WeeklyLine line={line} colorize />;
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

// Key Stories link straight to the daily brief that carried the story.
function KeyStoryLine({ line, weekStart, weekEnd, onOpenArticle }) {
  const isBullet    = line.startsWith("•");
  const inner       = isBullet ? line.slice(1).trim() : line;
  const displayHtml = inner.replace(/<a[^>]*>.*?<\/a>/g, "").replace(/\s*–?\s*$/, "").trim();
  const plainTitle  = displayHtml.replace(/<[^>]*>/g, "").replace(/&[a-z#0-9]+;/gi, " ").trim();

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

// ── Section list — render an already-filtered list of weekly sections ────────
export default function WeeklySections({ sections, weekly, onOpenArticle }) {
  const rot        = rotMeta(weekly.rotation);
  const isMovers   = (s) => s.hdr.startsWith("🏆");
  const isKeyStory = (s) => s.hdr.startsWith("📰");
  const isMarket   = (s) => s.hdr.startsWith("📊");

  if (!sections.length) return <p className="pw-empty">No content for this section.</p>;

  return (
    <>
      {sections.map((s, i) => (
        <section key={i} className={`pw-section pw-section--${sectionTone(s.hdr)}`}>
          <div className="pw-section-hdr">
            {stripSectionEmoji(wDecode(s.hdr))}
            {isMarket(s) && (
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
        </section>
      ))}
    </>
  );
}
