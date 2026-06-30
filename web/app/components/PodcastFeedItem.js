"use client";

import { fmtAgo } from "../../lib/format";
import { ChevronIcon, MicIcon } from "./icons";

const SENT = {
  bullish: { cls: "bull", glyph: "▲", label: "Bullish" },
  bearish: { cls: "bear", glyph: "▼", label: "Bearish" },
  neutral: { cls: "neut", glyph: "–", label: "Neutral" },
  mixed:   { cls: "mix",  glyph: "◇", label: "Mixed" },
};

// ── Podcast row in the daily feed — reads like a news bullet ─────────────────
export default function PodcastFeedItem({ podcast, selected, cursor, onSelect }) {
  const a = podcast.analysis || {};
  const sent = SENT[a.sentiment] || SENT.mixed;
  const themes = (a.themes || []).slice(0, 3);
  const ago = fmtAgo(podcast.published_at);

  return (
    <li
      className={`bullet bullet--pod${selected ? " bullet--selected" : ""}${cursor ? " bullet--cursor" : ""}`}
      onClick={e => { e.stopPropagation(); onSelect?.(); }}
      role="button"
      tabIndex={0}
      onKeyDown={e => (e.key === "Enter" || e.key === " ") && onSelect?.()}
      aria-pressed={selected}
    >
      <span className="bullet-importance bullet-sev--pod" aria-hidden="true" />
      <div className="bullet-layout">
        <div className="bullet-main">
          <span className="bullet-pod-eyebrow">
            <span className="bullet-pod-mic" aria-hidden><MicIcon /></span>
            Podcast · {podcast.channel}
          </span>
          <h3 className="bullet-title">{podcast.title}</h3>
          {a.tldr && <p className="bullet-text">{a.tldr}</p>}
          <div className="bullet-inline-tags">
            {themes.map((t, i) => <span key={i} className="bullet-cat">{t}</span>)}
            <span className={`bullet-pod-sent bullet-pod-sent--${sent.cls}`}>
              <span aria-hidden>{sent.glyph}</span> {sent.label}
            </span>
            {ago && <time className="bullet-time" dateTime={podcast.published_at} suppressHydrationWarning>{ago}</time>}
          </div>
        </div>
        <div className="bullet-aside">
          <span className={`pod-sent-ring pod-sent-ring--${sent.cls}`} aria-hidden>{sent.glyph}</span>
          <span className="bullet-chevron" aria-hidden="true"><ChevronIcon /></span>
        </div>
      </div>
    </li>
  );
}
