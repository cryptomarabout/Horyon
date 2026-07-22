import { Suspense } from "react";
import { getLatestDigest, getBulletAnalyses, getBulletTimes, listDigests, getPodcastsForDate, getAudioBriefing, latestDate, getDigestUpdates } from "../lib/db";
import { parseDigest, timeAgo, sourceLabel } from "../lib/digest";
import { buildProjectHints } from "../lib/projects";
import BulletFeed from "./components/BulletFeed";
import FeedSkeleton from "./components/FeedSkeleton";

export const dynamic = "force-dynamic";

const SITE_URL = "https://app.horyon.xyz";
const DESCRIPTION =
  "Crypto market intelligence from 100+ sources. The day's onchain, DeFi, regulatory " +
  "and market news, ranked by importance and written up with sourced analysis. " +
  "Every figure ties back to a source.";

// generateMetadata is async so we can pin the OG image to today's date. Without
// a ?date= param social platforms cache /api/og at that bare URL and keep serving
// stale cards on subsequent days even after the digest rotates.
export async function generateMetadata() {
  const date = await latestDate();
  const ogUrl = date ? `${SITE_URL}/api/og?date=${date}` : `${SITE_URL}/api/og`;
  return {
    title: "Horyon · Crypto Market Intelligence",
    description: DESCRIPTION,
    alternates: { canonical: SITE_URL },
    openGraph: {
      type: "website",
      title: "Horyon · Crypto Market Intelligence",
      description: DESCRIPTION,
      url: SITE_URL,
      images: [{ url: ogUrl, width: 1200, height: 628, alt: "Horyon · Crypto Market Intelligence" }],
    },
    twitter: {
      images: [ogUrl],
    },
  };
}

// Synchronous shell: returns immediately so the layout + a feed skeleton flush as the
// first byte, while the data-heavy feed streams in via <Suspense>.
export default function Home() {
  return (
    <article className="digest">
      <Suspense fallback={<FeedSkeleton />}>
        <HomeFeed />
      </Suspense>
    </article>
  );
}

async function HomeFeed() {
  // Wave 1: digest + date list. Project hints (entity/category chips) are built in wave 2
  // once we have the parsed bullets — see below.
  const [row, items] = await Promise.all([getLatestDigest(), listDigests()]);
  if (!row) return <div className="empty">No digests yet. Check back after the next run.</div>;

  const d = row.date;
  const { bullets } = parseDigest(row.content);

  // Wave 2: everything that needs the parsed bullet list. buildProjectHints is now 100% DB
  // (zero external calls) + cached per date, so the entity/category chips are SSR'd into the
  // first paint rather than fetched client-side after hydration.
  const [bulletAnalyses, bulletTimes, podcasts, audio, hints, updates] = await Promise.all([
    getBulletAnalyses(d),
    getBulletTimes(bullets.map(b => b.link)),
    getPodcastsForDate(d),
    getAudioBriefing(d),
    buildProjectHints(d, bullets),
    getDigestUpdates(d),
  ]);

  const enrichedBullets = bullets.map(b => ({
    title: b.title, body: b.body, hack: b.hack,
    link: b.link, src: sourceLabel(b.link),
    ts: b.link ? (bulletTimes[b.link] ?? null) : null,
  }));

  return (
    <BulletFeed
      bullets={enrichedBullets}
      analyses={bulletAnalyses}
      podcasts={podcasts}
      items={items}
      currentDate={row.date}
      signalsAgo={timeAgo(row.created_at)}
      audio={audio}
      initialHints={hints}
      updates={updates}
    />
  );
}
