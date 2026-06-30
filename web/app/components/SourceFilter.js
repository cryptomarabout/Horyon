"use client";

import { XIcon } from "./icons";

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
      {!allOn && (
        <button
          type="button"
          className="srcfilter-reset"
          onClick={onAll}
          aria-label="Reset source filter — show all"
          title="Show all sources"
        >
          <span className="srcfilter-reset-ic" aria-hidden>↺</span>
          <span>Reset</span>
        </button>
      )}
    </div>
  );
}
