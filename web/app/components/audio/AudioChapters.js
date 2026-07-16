"use client";

import { useState } from "react";
import { fmt, chapterDeepLink } from "../../../lib/audio";

// Expanded chapter list — each row jumps the player to its start; an optional
// ↗ opens the matching news story in the RightPanel (when onOpenStory is given);
// a 🔗 copies a shareable deep link that lands a listener mid-episode at this chapter.
export default function AudioChapters({ chaps, activeChap, date, variant, onJump, onOpenStory }) {
  const [copied, setCopied] = useState(-1);

  const copyLink = async (i, start) => {
    try {
      const url = new URL(chapterDeepLink(date, variant, start), window.location.origin).href;
      await navigator.clipboard.writeText(url);
      setCopied(i);
      setTimeout(() => setCopied((c) => (c === i ? -1 : c)), 1500);
    } catch {}
  };

  return (
    <ol className="audio-chapters">
      {chaps.map((c, i) => (
        <li key={i} className="audio-chapter-row">
          <button
            className={`audio-chapter${i === activeChap ? " active" : ""}`}
            onClick={() => onJump(c.start)}
          >
            <span className="audio-chapter-time">{fmt(c.start)}</span>
            <span className="audio-chapter-body">
              <span className="audio-chapter-title">{c.title}</span>
              {c.entities?.length > 0 && (
                <span className="audio-chapter-entities">
                  {c.entities.map((e, j) => (
                    <span key={j} className="audio-chapter-entity">{e}</span>
                  ))}
                </span>
              )}
            </span>
          </button>
          {date && (
            <button
              className="audio-chapter-link"
              onClick={ev => { ev.stopPropagation(); copyLink(i, c.start); }}
              title="Copy link to this chapter"
              aria-label={`Copy link to chapter: ${c.title}`}
            >
              {copied === i ? "✓" : "🔗"}
            </button>
          )}
          {onOpenStory && c.bullet_title && (
            <button
              className="audio-chapter-open"
              onClick={ev => { ev.stopPropagation(); onOpenStory(c.bullet_title); }}
              title="Open full story"
              aria-label={`Open full story: ${c.title}`}
            >
              ↗
            </button>
          )}
        </li>
      ))}
    </ol>
  );
}
