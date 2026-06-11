import { searchProjectInfo, searchEntityMemory } from "./db";

// Fetch project hints for every bullet at SSR time.
// All DB queries run in parallel; chain API and price API each fire once.
export async function buildProjectHints(bullets) {
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
        let logo = e.logo_url || null;
        if (!logo && e.twitter_handle) {
          // strip leading @ if present
          const handle = e.twitter_handle.startsWith("@")
            ? e.twitter_handle.slice(1)
            : e.twitter_handle;
          logo = `https://unavatar.io/twitter/${handle}`;
        }
        const handle = e.twitter_handle?.startsWith("@")
          ? e.twitter_handle.slice(1)
          : e.twitter_handle;
        const url = handle
          ? `https://x.com/${handle}`
          : `https://defillama.com/protocol/${e.slug}`;
        return {
          slug: e.slug,
          name: e.name,
          type: e.type,
          logo,
          url,
          // signals to BulletItem that this entity has no DeFiLlama backing
          isMemoryOnly: !e.logo_url && !e.twitter_handle,
          category: e.category || null,
        };
      });

    return { protocols, chains, entityTags };
  });
}
