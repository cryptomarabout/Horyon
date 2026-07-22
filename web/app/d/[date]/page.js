import { Suspense, cache } from "react";
import { notFound } from "next/navigation";
import { getDigest, getBulletAnalyses, getBulletTimes, listDigests, getPodcastsForDate, getAudioBriefing, getDigestUpdates } from "../../../lib/db";
import { parseDigest, timeAgo, sourceLabel, isValidDigestDate } from "../../../lib/digest";
import { buildProjectHints } from "../../../lib/projects";
import BulletFeed from "../../components/BulletFeed";
import FeedSkeleton from "../../components/FeedSkeleton";

// De-dupe the digest read: generateMetadata and the page render both need it, and without
// memoization that's two identical queries per request. React cache() collapses them into one.
const getDigestCached = cache(getDigest);

// ── Per-digest metadata (SEO + social cards) ────────────────────────────────
const SITE_URL = "https://app.horyon.xyz";
const MONTHS_LONG = ["January","February","March","April","May","June","July",
  "August","September","October","November","December"];

function prettyDate(dateStr) {
  const d = new Date(dateStr + "T00:00:00Z");
  return `${MONTHS_LONG[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}

export async function generateMetadata({ params }) {
  if (!isValidDigestDate(params.date)) return {};
  const row = await getDigestCached(params.date);
  if (!row) return {};

  const { bullets } = parseDigest(row.content);
  const title = `Crypto Intelligence · ${prettyDate(params.date)}`;
  // Lead with the day's top headlines so the snippet/social description is substantive.
  const lead = bullets.slice(0, 3).map(b => b.title).filter(Boolean).join(" · ");
  const description = lead
    ? `${lead}. — Horyon's daily crypto-intelligence digest.`.slice(0, 300)
    : `Horyon's crypto-intelligence digest for ${prettyDate(params.date)}.`;
  const canonical = `${SITE_URL}/d/${params.date}`;
  const ogImage = `${SITE_URL}/api/og?date=${params.date}`;

  return {
    title,
    description,
    alternates: { canonical },
    openGraph: {
      type: "article",
      title: `${title} · Horyon`,
      description,
      url: canonical,
      publishedTime: params.date,
      images: [{ url: ogImage, width: 1200, height: 628, alt: title }],
    },
    twitter: {
      card: "summary_large_image",
      title: `${title} · Horyon`,
      description,
      images: [ogImage],
    },
  };
}

// ── Page ──────────────────────────────────────────────────────────────────
// Synchronous shell: emits the JSON-LD + a feed skeleton as the first byte, then streams
// the data-heavy feed in via <Suspense> rather than blocking the whole render on the slowest
// query. The date is validated here so /d/garbage still 404s before any DB work.
export default function DigestPage({ params }) {
  // Reject malformed date params before they hit a SQL date cast (otherwise a public
  // URL like /d/garbage — or a numeric-but-invalid one like /d/2026-13-99 — throws and
  // surfaces as a 500 / naked error page instead of a clean 404).
  if (!isValidDigestDate(params.date)) notFound();

  const canonicalUrl = `${SITE_URL}/d/${params.date}`;
  const digestTitle = `Crypto Intelligence · ${prettyDate(params.date)}`;

  return (
    <>
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify({
        "@context": "https://schema.org",
        "@type": "Article",
        headline: digestTitle,
        datePublished: params.date,
        url: canonicalUrl,
        publisher: { "@type": "Organization", name: "Horyon", url: SITE_URL },
      }) }}
    />
    <article className="digest">
      <Suspense fallback={<FeedSkeleton />}>
        <DigestFeed date={params.date} />
      </Suspense>
    </article>
    </>
  );
}

async function DigestFeed({ date }) {
  // Wave 1: everything that only needs the date (not parsed bullets). listDigests is cached;
  // getDigestCached shares its read with generateMetadata. tvl/market are gone — BulletFeed
  // never used them; they live only in the footer ticker (layout.js).
  const [row, items, podcasts, audio, bulletAnalyses] = await Promise.all([
    getDigestCached(date),
    listDigests(),
    getPodcastsForDate(date),
    getAudioBriefing(date),
    getBulletAnalyses(date),
  ]);
  if (!row) notFound();

  const { bullets } = parseDigest(row.content);

  // Wave 2: needs the parsed bullet list. buildProjectHints is now 100% DB (zero external
  // calls) + cached per date, so we await it here and SSR the entity/category chips into
  // the first paint instead of the old client fetch that made chips pop in after hydration.
  const [bulletTimes, hints, updates] = await Promise.all([
    getBulletTimes(bullets.map(b => b.link)),
    buildProjectHints(row.date, bullets),
    getDigestUpdates(date),
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
