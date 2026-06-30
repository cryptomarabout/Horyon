import { unstable_cache } from "next/cache";
import { searchProjectInfo, searchEntityMemory } from "./db";

// Fetch project hints for every bullet at SSR time.
// All DB queries run in parallel; chain API and price API each fire once.
async function _buildProjectHints(bullets) {
  if (!bullets.length) return [];

  // One chain list call, cached 30 min by Next.js fetch cache
  let rankedChains = [];
  try {
    const resp = await fetch("https://api.llama.fi/v2/chains", {
      next: { revalidate: 1800 },
    });
    if (resp.ok) {
      const all = await resp.json();
      rankedChains = [...all]
        .sort((a, b) => (b.tvl || 0) - (a.tvl || 0))
        .slice(0, 100)
        .map((c, i) => ({ ...c, rank: i + 1 }));
    }
  } catch {}

  // Search text: title + first 80 chars of body gives entity signal without bloating the query.
  const searchTexts = bullets.map(b =>
    b.title + (b.body ? " " + b.body.slice(0, 80) : "")
  );

  // All DB queries in parallel — DeFiLlama protocols + entity_memory per bullet
  const [protocolResults, entityResults] = await Promise.all([
    Promise.all(searchTexts.map(t => searchProjectInfo(t).catch(() => ({ protocols: [] })))),
    Promise.all(searchTexts.map(t => searchEntityMemory(t).catch(() => []))),
  ]);

  // One batch price call for all unique gecko_ids across all bullets
  const allProtocols = protocolResults.flatMap(r => r.protocols || []);
  const geckoIds = [...new Set(
    allProtocols.filter(p => p.gecko_id).map(p => p.gecko_id)
  )];
  let priceMap = {};
  if (geckoIds.length) {
    try {
      const coins = geckoIds.map(id => `coingecko:${id}`).join(",");
      const resp = await fetch(
        `https://coins.llama.fi/prices/current/${coins}`,
        { next: { revalidate: 300 } }
      );
      if (resp.ok) {
        const data = await resp.json();
        priceMap = data.coins || {};
      }
    } catch {}
  }

  return bullets.map((b, i) => {
    const protocols = (protocolResults[i].protocols || []).map(p => ({
      ...p,
      price: p.gecko_id
        ? (priceMap[`coingecko:${p.gecko_id}`]?.price ?? null)
        : null,
    }));

    const chains = rankedChains
      .filter(c => {
        const name = (c.name || "").trim();
        if (name.length < 3) return false;
        // Word-boundary check prevents "Base" matching "Based" or "Database"
        const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        try { return new RegExp(`\\b${escaped}\\b`, "i").test(b.title); }
        catch { return false; }
      })
      .slice(0, 3)
      .map(c => ({
        name: c.name,
        tvl: c.tvl ?? null,
        rank: c.rank,
        tokenSymbol: c.tokenSymbol || null,
        logo: `https://icons.llamao.fi/icons/chains/rsz_${c.name.toLowerCase()}.jpg`,
        url: `https://defillama.com/chain/${encodeURIComponent(c.name)}`,
      }));

    // entity_memory entities: include those not already covered by DeFiLlama protocols.
    // Deduplicate by slug (now returned by searchProjectInfo) AND by normalised name
    // (strip hyphens/spaces for fuzzy match: "ether-fi" ≈ "etherfi" ≈ "ether fi").
    const normalise = s => s.toLowerCase().replace(/[-.\s]/g, "");
    const defillamaNames = new Set(protocols.map(p => p.name.toLowerCase()));
    const defillamaSlugNorms = new Set(protocols.map(p => normalise(p.slug || p.name)));
    const entityTags = (entityResults[i] || [])
      .filter(e =>
        !defillamaNames.has(e.name.toLowerCase()) &&
        !defillamaSlugNorms.has(normalise(e.slug)) &&
        !defillamaSlugNorms.has(normalise(e.name))
      )
      .map(e => {
        const handle = e.twitter_handle?.startsWith("@")
          ? e.twitter_handle.slice(1)
          : e.twitter_handle;
        // Avatar fallback chain — EntityAvatar walks it in order, then draws a monogram
        // if every URL fails, so EVERY entity ends up with an image:
        //   1. /api/avatar/<slug> — the bot-mirrored avatar served from our own DB, but
        //      ONLY when entity_avatars actually has it (avatar_cached). app/avatars.py
        //      resolves the Twitter pic server-side, so the browser NEVER hits unavatar.io
        //      (preserves zero-egress + avoids unavatar's per-client rate-limit). Gating on
        //      the flag avoids a guaranteed-404 request for not-yet-mirrored entities.
        //   2. logo_url = COALESCE(DeFiLlama protocol logo, CoinGecko-seeded entity logo).
        // Mirrors NarrativeView/NarrativePanel.
        const avatars = [
          e.avatar_cached ? `/api/avatar/${e.slug}` : null,
          e.logo_url,
        ].filter(Boolean);
        const url = handle
          ? `https://x.com/${handle}`
          : `https://defillama.com/protocol/${e.slug}`;
        return {
          slug: e.slug,
          name: e.name,
          type: e.type,
          avatars,
          url,
          category: e.category || null,
        };
      });

    return { protocols, chains, entityTags };
  });
}

// Cache project hints per digest date — once a digest is published the bullets
// don't change, so 1h TTL is safe and avoids the N*2 parallel DB queries on
// every subsequent page load for the same date.
export function buildProjectHints(date, bullets) {
  return unstable_cache(
    () => _buildProjectHints(bullets),
    ["horyon-project-hints", date],
    { revalidate: 3600 }
  )();
}
