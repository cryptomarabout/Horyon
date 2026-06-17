// Shared entity-graph taxonomy (pure data — safe in server or client components).
// Node color encodes entity type; the legend doubles as the type filter.

export const TYPE_META = {
  protocol: { label: "Protocols", cls: "protocol" },
  chain:    { label: "Chains",    cls: "chain" },
  exchange: { label: "Exchanges", cls: "exchange" },
  fund:     { label: "Funds",     cls: "fund" },
  dao:      { label: "DAOs",      cls: "dao" },
  person:   { label: "People",    cls: "person" },
  other:    { label: "Other",     cls: "other" },
};

export const TYPES = ["protocol", "chain", "exchange", "fund", "dao", "person", "other"];

// Avatar fallback chain for a node: real logo (DeFiLlama/CoinGecko) → the Twitter
// avatar via unavatar.io (which aggregates Nitter/X/etc.) → monogram (handled by
// the caller when the list is exhausted). `fallback=false` makes unavatar 404 when
// it has no picture, so we drop to the monogram instead of its generic grey blob.
export function avatarCandidates(node) {
  const out = [];
  if (node?.logoUrl) out.push(node.logoUrl);
  const h = (node?.twitterHandle || "").replace(/^@/, "").trim();
  if (h) out.push(`https://unavatar.io/twitter/${encodeURIComponent(h)}?fallback=false`);
  return out;
}
