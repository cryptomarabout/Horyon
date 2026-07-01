import { unstable_cache } from "next/cache";
import { searchProjectInfo, searchEntityMemory, getChainDirectory } from "./db";

// Build project hints for every bullet — 100% from our own Postgres, ZERO per-request
// external calls (the web container's zero-egress rule). Protocols/categories/entities
// come from defillama_protocols + entity_memory; the chain roster comes from entity_memory
// (~79 chains) via getChainDirectory, so EVERY tracked chain — not just the 6 we snapshot
// TVL for — gets its icons.llamao.fi chip logo. Live token price is intentionally gone —
// the panel price line self-hides when absent.
async function _buildProjectHints(bullets) {
  if (!bullets.length) return [];

  // Full chain name universe from our own DB (entity_memory), TVL-then-coverage ranked.
  // The chip logo is a deterministic icons.llamao.fi URL built from the name, so no live
  // api.llama.fi chain-list call is needed to restore broad chain-logo coverage.
  let rankedChains = [];
  try {
    const dir = await getChainDirectory();
    rankedChains = dir.map((c, i) => ({ name: c.name, tvl: c.tvl_usd ?? null, rank: i + 1 }));
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

  return bullets.map((b, i) => {
    // No live price enrichment — defillama_protocols rows carry no `price`, so the panel's
    // `fmtPrice(p.price)` guard simply hides the price line (see BulletPanel).
    const protocols = protocolResults[i].protocols || [];

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
        tokenSymbol: null,
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
