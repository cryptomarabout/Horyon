import { Suspense } from "react";
import { notFound } from "next/navigation";
import { getDigest, getTvlWithChange, getBulletAnalyses, getBulletTimes, listDigests, getPodcastsForDate } from "../../../lib/db";
import { getMarketData } from "../../../lib/prices";
import { parseDigest } from "../../../lib/digest";
import { buildProjectHints } from "../../../lib/projects";
import BulletFeed from "../../components/BulletFeed";

export const dynamic = "force-dynamic";

function timeAgo(isoStr) {
  if (!isoStr) return null;
  try {
    const diffMs = Date.now() - new Date(isoStr + "Z").getTime();
    if (diffMs < 0) return "just now";
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1)  return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const hrs = Math.floor(diffMin / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch { return null; }
}

// ── Source label ───────────────────────────────────────────────────────────
const DOMAIN_NAMES = {
  "cointelegraph.com": "CoinTelegraph", "coindesk.com": "CoinDesk",
  "theblock.co": "The Block", "decrypt.co": "Decrypt",
  "blockworks.co": "Blockworks", "beincrypto.com": "BeInCrypto",
  "cryptoslate.com": "CryptoSlate", "bitcoinmagazine.com": "Bitcoin Magazine",
  "dlnews.com": "DL News", "thedefiant.io": "The Defiant",
  "bankless.com": "Bankless", "unchainedcrypto.com": "Unchained",
  "protos.com": "Protos", "wired.com": "Wired",
  "reuters.com": "Reuters", "bloomberg.com": "Bloomberg",
};

function sourceLabel(url) {
  if (!url) return null;
  try {
    const u    = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    if (host === "x.com" || host === "twitter.com" || host.includes("nitter.")) {
      const user = u.pathname.split("/").filter(Boolean)[0];
      if (user && !["i","search","explore","home"].includes(user))
        return { type: "twitter", name: "@" + user };
      return { type: "twitter", name: "X" };
    }
    if (DOMAIN_NAMES[host]) return { type: "news", name: DOMAIN_NAMES[host] };
    const base = host.split(".")[0];
    return { type: "news", name: base.charAt(0).toUpperCase() + base.slice(1) };
  } catch { return null; }
}

// ── Page ──────────────────────────────────────────────────────────────────
export default async function DigestPage({ params }) {
  const [row, tvl, items, podcasts] = await Promise.all([
    getDigest(params.date),
    getTvlWithChange(),
    listDigests(),
    getPodcastsForDate(params.date),
  ]);
  if (!row) notFound();

  const { bullets } = parseDigest(row.content);
  const [projectHints, bulletAnalyses, market, bulletTimes] = await Promise.all([
    buildProjectHints(bullets),
    getBulletAnalyses(params.date),
    getMarketData(),
    getBulletTimes(bullets.map(b => b.link)),
  ]);

  const enrichedBullets = bullets.map(b => ({
    title: b.title, body: b.body, hack: b.hack,
    link: b.link, src: sourceLabel(b.link),
    ts: b.link ? (bulletTimes[b.link] ?? null) : null,
  }));

  const signalsAgo = timeAgo(row.created_at);

  return (
    <article className="digest">
      <Suspense fallback={null}>
        <BulletFeed
          bullets={enrichedBullets}
          projectHints={projectHints}
          analyses={bulletAnalyses}
          podcasts={podcasts}
          items={items}
          currentDate={row.date}
          signalsAgo={signalsAgo}
          tvl={tvl}
          market={market}
        />
      </Suspense>
    </article>
  );
}
