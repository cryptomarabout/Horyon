import { listWeeklyDigests, getWeeklyMarketSnapshots, getTvlWithChange, getTvlSnapshot } from "../../lib/db";
import { deDash } from "../../lib/weekly";
import WeeklyView from "../components/WeeklyView";

export const dynamic = "force-dynamic";

const _WEEKLY_DESC = "Weekly macro review: BTC dominance, ETH/BTC ratio, DeFi TVL, and protocol highlights from Horyon's crypto-intelligence feed.";

export const metadata = {
  // Bare label — the root layout's title template appends " · Horyon" (avoids a doubled brand).
  title: "Weekly Briefs",
  description: _WEEKLY_DESC,
  alternates: { canonical: "https://app.horyon.xyz/weekly" },
  openGraph: {
    type: "website",
    title: "Weekly Briefs · Horyon",
    description: _WEEKLY_DESC,
    url: "https://app.horyon.xyz/weekly",
  },
};

export default async function WeeklyPage() {
  let weeklies = [];
  let snapshots = [];
  let tvlRows = [];
  try {
    [weeklies, snapshots, tvlRows] = await Promise.all([
      listWeeklyDigests(),
      getWeeklyMarketSnapshots(),
      getTvlWithChange(),
    ]);
  } catch { /* empty */ }

  // Fold the structured snapshot (when present) into its week; the view prefers
  // it over parsed prose. Absent column → snapshots is [] → graceful fallback.
  // Em dashes are forbidden in the report — strip them from the content at the
  // source so neither the rendered body nor the serialized payload carries any.
  const snapByWeek = new Map((snapshots || []).map((s) => [s.week_start, s.market_snapshot]));
  const merged = (weeklies || []).map((w) => ({
    ...w,
    content: w.content ? deDash(w.content) : w.content,
    market_snapshot: snapByWeek.get(w.week_start) || null,
  }));

  // Total DeFi TVL reading (zero-egress safe) for the Market Snapshot. Prefer the
  // day-over-day variant; if it's unavailable, fall back to the latest level alone.
  let total = (tvlRows || []).find((r) => r.chain === "total") || null;
  if (!total) {
    const snap = (await getTvlSnapshot()).find((r) => r.chain === "total");
    if (snap) total = { tvl_now: snap.tvl_usd, pct: null };
  }
  const defiTvl = total ? { tvl_now: total.tvl_now, pct: total.pct } : null;

  return (
    <article className="digest">
      <WeeklyView weeklies={merged} defiTvl={defiTvl} />
    </article>
  );
}
