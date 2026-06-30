"use client";

import { useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import EmptyState from "./ui/EmptyState";
import ReportMasthead from "./weekly/ReportMasthead";
import MarketSnapshot from "./weekly/MarketSnapshot";
import ReportContents from "./weekly/ReportContents";
import ReportSections from "./weekly/ReportSections";
import IssueNav from "./weekly/IssueNav";
import useSwipeNav from "../../lib/useSwipeNav";
import {
  fmtWeekRange, parseWeeklySections, reportModel, snapshotCells,
  marketDek, marketTone, deDash,
} from "../../lib/weekly";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtPublished(iso) {
  if (!iso) return null;
  const m = iso.slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  return `${MONTHS[+m[2] - 1]} ${+m[3]}, ${m[1]}`;
}

// ── Weekly Market Review — institutional research-publication frame ──────────
// A single flowing report (masthead → market snapshot → numbered sections →
// colophon), navigated by an in-page Contents rail + scroll-spy, with archive
// (issue) navigation on the cover. No tabs, no right panel. defiTvl is the DB's
// total-TVL reading (zero-egress safe), folded into the Market Snapshot.
export default function WeeklyView({ weeklies = [], defiTvl = null }) {
  const router = useRouter();
  const [idx, setIdx] = useState(0); // 0 = newest = current issue

  const selected = weeklies[idx] || null;

  const sections = useMemo(
    () => (selected ? reportModel(parseWeeklySections(selected.content)) : []),
    [selected]
  );
  const market = useMemo(() => sections.find((s) => s.kind === "market") || null, [sections]);
  const cells = useMemo(
    () => snapshotCells({
      snapshot: selected?.market_snapshot,
      marketLines: market?.lines,
      defiTvl,
    }),
    [selected, market, defiTvl]
  );

  const goto = useCallback((i) => {
    setIdx(i);
    document.querySelector(".feed-scroll")?.scrollTo({ top: 0 });
  }, []);

  const newer = idx > 0 ? idx - 1 : null;
  const older = idx < weeklies.length - 1 ? idx + 1 : null;
  const swipe = useSwipeNav(
    useCallback(() => { if (newer != null) goto(newer); }, [newer, goto]),
    useCallback(() => { if (older != null) goto(older); }, [older, goto]),
  );

  const openDigest = useCallback((art) => { if (art?.date) router.push(`/d/${art.date}`); }, [router]);

  if (!selected) {
    return (
      <div className="feed-grid feed-grid--solo">
        <div className="feed-left">
          <div className="feed-scroll">
            <EmptyState variant="feed" glyph="≋">
              No weekly issues yet.<br />The first runs Monday 07:30 UTC.
            </EmptyState>
          </div>
        </div>
      </div>
    );
  }

  const dek = deDash(marketDek(market?.lines))
    || "A seven-day review of crypto market structure, capital flows, and the narratives shaping them.";
  const tone = marketTone(selected.rotation, market?.lines, selected.market_snapshot);

  return (
    <div className="feed-grid feed-grid--solo">
      <div className="feed-left">
        <div
          className="feed-scroll"
          onTouchStart={swipe.onTouchStart}
          onTouchEnd={swipe.onTouchEnd}
        >
          {sections.length > 1 && (
            <div className="feed-head">
              <ReportContents sections={sections} />
            </div>
          )}

          <article className="wr">
            <ReportMasthead
              title="Weekly Market Review"
              dek={dek}
              range={fmtWeekRange(selected.week_start, selected.week_end)}
              published={fmtPublished(selected.created_at)}
              tone={tone}
              issueNav={<IssueNav weeklies={weeklies} idx={idx} onPick={goto} />}
            />

            <MarketSnapshot cells={cells} />

            <ReportSections sections={sections} weekly={selected} onOpenArticle={openDigest} />

            <footer className="wr-colophon">
              Horyon · Crypto Market Intelligence<br />
              Compiled from ~107 sources across market data, on-chain analytics, and primary reporting.
              Figures are point-in-time; not investment advice.
            </footer>
          </article>
        </div>
      </div>
    </div>
  );
}
