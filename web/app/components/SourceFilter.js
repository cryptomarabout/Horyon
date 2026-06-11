"use client";

// ── Source filter — channel selector for the daily feed ─────────────────────
// All on by default. "All" resets to everything; individual chips toggle.

function NewsIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 2.5h7a1 1 0 0 1 1 1V13a1 1 0 0 0 1 1H4a1 1 0 0 1-1-1V2.5z" />
      <line x1="5" y1="5.5" x2="9" y2="5.5" /><line x1="5" y1="8" x2="9" y2="8" />
      <line x1="5" y1="10.5" x2="7.5" y2="10.5" /><path d="M11 6.5h1.5a.5.5 0 0 1 .5.5V13" />
    </svg>
  );
}
function XIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.742l7.737-8.835L1.254 2.25H8.08l4.259 5.626L18.243 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
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

const SOURCES = [
  { key: "news",     label: "News",     Icon: NewsIcon },
  { key: "tweets",   label: "Tweets",   Icon: XIcon },
  { key: "podcasts", label: "Podcasts", Icon: MicIcon },
];

export default function SourceFilter({ active, counts = {}, onToggle, onAll }) {
  const allOn = SOURCES.every(s => active[s.key]);

  return (
    <div className="srcfilter" role="group" aria-label="Filter by source" onClick={e => e.stopPropagation()}>
      <button
        type="button"
        className={`srcfilter-chip srcfilter-all${allOn ? " is-active" : ""}`}
        aria-pressed={allOn}
        onClick={onAll}
      >
        All
      </button>
      {SOURCES.map(({ key, label, Icon }) => {
        const on = !!active[key];
        const n = counts[key] ?? 0;
        return (
          <button
            key={key}
            type="button"
            className={`srcfilter-chip srcfilter-chip--${key}${on ? " is-active" : ""}`}
            aria-pressed={on}
            onClick={() => onToggle(key)}
            disabled={n === 0}
            title={n === 0 ? `No ${label.toLowerCase()} today` : `${n} ${label.toLowerCase()}`}
          >
            <span className="srcfilter-ic" aria-hidden><Icon /></span>
            <span>{label}</span>
            {n > 0 && <span className="srcfilter-n">{n}</span>}
          </button>
        );
      })}
    </div>
  );
}
