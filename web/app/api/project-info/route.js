import { NextResponse } from "next/server";
import { searchProjectInfo } from "../../../lib/db";

// ── Chain data ─────────────────────────────────────────────────────────────
// Fetch top 60 chains by TVL (cached 30 min), then find which ones appear
// verbatim in the headline. Case-sensitive so "base layer" ≠ "Base" chain.
async function fetchMatchingChains(title) {
  try {
    const resp = await fetch("https://api.llama.fi/v2/chains", {
      next: { revalidate: 1800 },
    });
    if (!resp.ok) return [];
    const all = await resp.json();
    const sorted = [...all].sort((a, b) => (b.tvl || 0) - (a.tvl || 0));

    return sorted
      .slice(0, 60)
      .map((chain, idx) => ({ ...chain, rank: idx + 1 }))
      .filter(chain => {
        const name = (chain.name || "").trim();
        return name.length >= 3 && title.includes(name);
      })
      .slice(0, 3)
      .map(chain => ({
        name: chain.name,
        tvl: chain.tvl ?? null,
        rank: chain.rank,
        tokenSymbol: chain.tokenSymbol || null,
        // DeFiLlama icon convention; onError in the component hides missing ones
        logo: `https://icons.llamao.fi/icons/chains/rsz_${chain.name.toLowerCase()}.jpg`,
      }));
  } catch {
    return [];
  }
}

// ── Token prices ───────────────────────────────────────────────────────────
// Batch-fetch prices for any protocol that has a gecko_id, using DeFiLlama
// coins API (free, no key). Cached 5 min.
async function enrichWithPrices(protocols) {
  const withId = protocols.filter(p => p.gecko_id);
  if (!withId.length) return protocols;
  try {
    const coins = withId.map(p => `coingecko:${p.gecko_id}`).join(",");
    const resp = await fetch(
      `https://coins.llama.fi/prices/current/${coins}`,
      { next: { revalidate: 300 } }
    );
    if (!resp.ok) return protocols;
    const data = await resp.json();
    return protocols.map(p => ({
      ...p,
      price: p.gecko_id
        ? (data.coins?.[`coingecko:${p.gecko_id}`]?.price ?? null)
        : null,
    }));
  } catch {
    return protocols;
  }
}

// ── Route ──────────────────────────────────────────────────────────────────
export async function POST(req) {
  let title;
  try {
    ({ title } = await req.json());
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const t = title?.trim();
  if (!t) {
    return NextResponse.json({ error: "title is required" }, { status: 400 });
  }

  // DB protocols query and chain API fetch run in parallel
  const [dbResult, chains] = await Promise.all([
    searchProjectInfo(t),
    fetchMatchingChains(t),
  ]);

  // Price fetch depends on which protocols matched, so runs after
  const protocols = await enrichWithPrices(dbResult.protocols);

  return NextResponse.json({ protocols, chains });
}
