"use client";

import { useState } from "react";
import { parseDigest, timeAgo, updateSlotLabel, sourceLabel } from "../../lib/digest";

// ── Intraday updates timeline ───────────────────────────────────────────────
// Read-only strip above the daily feed showing the day's incremental updates
// (app/intraday.py — Midday / Evening). Each update's stored content is the same
// Telegram-HTML bullet block as the morning digest, so it parses with parseDigest.
// Deliberately NOT wired into the feed's selection/filter state — it's a lightweight
// "what changed since this morning" banner, collapsible, that never steals focus.
export default function IntradayUpdates({ updates = [] }) {
  const parsed = (updates || [])
    .map(u => ({ ...u, ...parseDigest(u.content) }))
    .filter(u => u.bullets.length);

  const [open, setOpen] = useState(true);
  if (!parsed.length) return null;

  const total = parsed.reduce((n, u) => n + u.bullets.length, 0);

  return (
    <section className="intraday" onClick={e => e.stopPropagation()}>
      <button
        type="button"
        className="intraday-head"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
      >
        <span className="intraday-chev" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="intraday-title">Intraday updates</span>
        <span className="intraday-count">
          {parsed.length} update{parsed.length !== 1 ? "s" : ""} · {total} new
        </span>
      </button>

      {open && (
        <div className="intraday-body">
          {parsed.map((u, i) => (
            <div className="intraday-block" key={u.created_at || u.seq || i}>
              <div className="intraday-slot">
                <span className="intraday-slot-label">{updateSlotLabel(u.created_at)}</span>
                {timeAgo(u.created_at) && (
                  // suppressHydrationWarning: timeAgo is clock-dependent, so the SSR string can
                  // differ from the hydration render across a minute boundary (repo pattern —
                  // same as the bullet <time> stamps).
                  <span className="intraday-slot-time" suppressHydrationWarning>
                    {timeAgo(u.created_at)}
                  </span>
                )}
              </div>
              <ul className="intraday-bullets">
                {u.bullets.map((b, j) => {
                  const src = sourceLabel(b.link);
                  return (
                    <li className={`intraday-bullet${b.hack ? " intraday-bullet--hack" : ""}`} key={j}>
                      <span className="intraday-bullet-title">
                        {b.hack ? "🚨 " : ""}{b.title}
                      </span>
                      {b.body && <span className="intraday-bullet-body"> — {b.body}</span>}
                      {b.link && src && (
                        <a
                          className="intraday-bullet-src"
                          href={b.link}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          {src.name}
                        </a>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
