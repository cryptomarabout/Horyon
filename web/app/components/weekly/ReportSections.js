"use client";

import { useCallback } from "react";
import MoversTable from "./MoversTable";
import {
  wDecode, deDash, colorizePcts, stripTrailingPeriod, parseMovers, parseStoryLine,
} from "../../../lib/weekly";

// Report display text: decode entities + strip em dashes (forbidden in the report).
const rt = (s) => deDash(wDecode(s));

// ── Per-kind body renderers ──────────────────────────────────────────────────

// Market: de-bulleted prose; the first paragraph carries an editorial drop-cap.
function MarketBody({ lines }) {
  const paras = lines
    .map((l) => (l.startsWith("•") ? l.slice(1).trim() : l).trim())
    .filter(Boolean);
  if (!paras.length) return <p className="wr-empty">No market commentary this week.</p>;
  return (
    <>
      {paras.map((p, i) => (
        <p key={i} className={`wr-prose${i === 0 ? " wr-lede" : ""}`}
           dangerouslySetInnerHTML={{ __html: rt(p) }} />
      ))}
    </>
  );
}

// DeFi / Narratives: clean editorial list with hairline rows + bold lead-ins.
function ListBody({ lines, colorize }) {
  const items = lines.map((l) => (l.startsWith("•") ? l.slice(1).trim() : l).trim()).filter(Boolean);
  if (!items.length) return <p className="wr-empty">Nothing notable this week.</p>;
  return (
    <div className="wr-list">
      {items.map((ln, i) => {
        const html = stripTrailingPeriod(colorize ? colorizePcts(rt(ln)) : rt(ln));
        return <div key={i} className="wr-li" dangerouslySetInnerHTML={{ __html: html }} />;
      })}
    </div>
  );
}

// Outlook (What To Watch): discrete watch points rather than one big quote block.
// The model usually writes this as one flowing paragraph, so we split it into
// sentences — each becomes a watch point with a gold marker, scanning like a
// research note's "what we're watching". A genuinely single thought stays a
// compact pull-quote.
//
// Split on sentence-final ". " only when the period follows a lowercase letter,
// digit or close-punct AND the next sentence starts with a capital/$ — so prices
// ("$2.20T", "55.8%") and initials ("U.S.") don't get cut.
function watchPoints(lines) {
  const items = lines.map((l) => (l.startsWith("•") ? l.slice(1).trim() : l).trim()).filter(Boolean);
  if (items.length > 1) return items;            // already bulleted by the model
  const one = items[0] || "";
  const parts = one
    .split(/(?<=[a-z0-9%)\]”"'])\.\s+(?=[A-Z“"$])/)
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length ? parts : items;
}

function OutlookBody({ lines }) {
  const points = watchPoints(lines);
  if (!points.length) return null;
  if (points.length === 1) {
    return (
      <div className="wr-outlook">
        <p dangerouslySetInnerHTML={{ __html: stripTrailingPeriod(rt(points[0])) }} />
      </div>
    );
  }
  return (
    <ul className="wr-watch">
      {points.map((p, i) => (
        <li key={i} className="wr-watch-item">
          <span className="wr-watch-mark" aria-hidden="true">→</span>
          <span dangerouslySetInnerHTML={{ __html: stripTrailingPeriod(rt(p)) }} />
        </li>
      ))}
    </ul>
  );
}

// Key Stories: numbered, click-through to the daily brief that carried the story
// (via /api/find-digest, preserving the prior behaviour); the source link stays
// reachable as a trailing ↗.
function StoriesBody({ lines, weekStart, weekEnd, onOpenArticle }) {
  const stories = lines.map(parseStoryLine).filter((s) => s.title);
  if (!stories.length) return <p className="wr-empty">No key stories logged this week.</p>;
  return (
    <div className="wr-stories">
      {stories.map((s, i) => (
        <Story key={i} n={i + 1} story={s} weekStart={weekStart} weekEnd={weekEnd}
               onOpenArticle={onOpenArticle} />
      ))}
    </div>
  );
}

function Story({ n, story, weekStart, weekEnd, onOpenArticle }) {
  const title = story.title.replace(/<[^>]*>/g, "").replace(/&[a-z#0-9]+;/gi, " ").trim();
  const open = useCallback(async () => {
    try {
      const r = await fetch("/api/find-digest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, week_start: weekStart, week_end: weekEnd }),
      });
      const data = await r.json();
      if (data?.date) onOpenArticle({ date: data.date, title });
    } catch { /* ignore */ }
  }, [title, weekStart, weekEnd, onOpenArticle]);

  return (
    <div className="wr-story" role="button" tabIndex={0}
         onClick={open} onKeyDown={(e) => e.key === "Enter" && open()}>
      <span className="wr-story-num">{String(n).padStart(2, "0")}</span>
      <span className="wr-story-title" dangerouslySetInnerHTML={{ __html: rt(story.title) }} />
      {story.href && (
        <a className="wr-story-src" href={story.href} target="_blank" rel="noopener noreferrer"
           onClick={(e) => e.stopPropagation()} aria-label="Open source">↗</a>
      )}
      <span className="wr-story-go" aria-hidden="true">→</span>
    </div>
  );
}

// ── Section dispatcher ───────────────────────────────────────────────────────
export default function ReportSections({ sections, weekly, onOpenArticle }) {
  if (!sections.length) return <p className="wr-empty">No content for this issue.</p>;
  return (
    <div className="wr-body">
      {sections.map((s) => {
        let body;
        if (s.kind === "market") body = <MarketBody lines={s.lines} />;
        else if (s.kind === "movers") {
          const { gainers, losers } = parseMovers(s.lines);
          body = <MoversTable gainers={gainers} losers={losers} />;
        } else if (s.kind === "defi") body = <ListBody lines={s.lines} colorize />;
        else if (s.kind === "narratives") body = <ListBody lines={s.lines} colorize={false} />;
        else if (s.kind === "stories")
          body = <StoriesBody lines={s.lines} weekStart={weekly.week_start}
                              weekEnd={weekly.week_end} onOpenArticle={onOpenArticle} />;
        else if (s.kind === "outlook") body = <OutlookBody lines={s.lines} />;
        else body = <ListBody lines={s.lines} colorize={false} />;

        return (
          <section key={s.id} id={`wr-${s.id}`} className="wr-section">
            <div className="wr-sec-head">
              <span className="wr-sec-num">{s.num}</span>
              <h2 className="wr-sec-title">{s.title}</h2>
            </div>
            {body}
          </section>
        );
      })}
    </div>
  );
}
