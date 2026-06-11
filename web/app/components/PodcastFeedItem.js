"use client";

function ChevronIcon() {
  return (
    <svg width="9" height="9" viewBox="0 0 12 12" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="4,2 8,6 4,10" />
    </svg>
  );
}

function MicIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="5.5" y="1.5" width="5" height="8" rx="2.5" />
      <path d="M3.5 7.5a4.5 4.5 0 0 0 9 0" /><line x1="8" y1="12" x2="8" y2="14.5" />
    </svg>
  );
}

const SENT = {
  bullish: { cls: "bull", glyph: "▲", label: "Bullish" },
  bearish: { cls: "bear", glyph: "▼", label: "Bearish" },
  neutral: { cls: "neut", glyph: "–", label: "Neutral" },
  mixed:   { cls: "mix",  glyph: "◇", label: "Mixed" },
};

function timeAgo(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const m = Math.floor((Date.now() - t) / 60000);
  if (m < 1) return "now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// ── Podcast row in the daily feed — reads like a news bullet ─────────────────
export default function PodcastFeedItem({ podcast, selected, cursor, onSelect }) {
  const a = podcast.analysis || {};
  const sent = SENT[a.sentiment] || SENT.mixed;
  const themes = (a.themes || []).slice(0, 3);
  const ago = timeAgo(podcast.published_at);

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
