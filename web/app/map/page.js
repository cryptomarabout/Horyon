import { unstable_cache } from "next/cache";
import { getEntityGraph, getEntityLeague } from "../../lib/db";
import EntityGraph from "../components/EntityGraph";

const _MAP_DESC = "Visual co-occurrence map of crypto entities — protocols, chains, funds, and people — across Horyon's recent intelligence digests.";

export const metadata = {
  // Bare label — the root layout's title template appends " · Horyon" (avoids a doubled brand).
  title: "Atlas",
  description: _MAP_DESC,
  alternates: { canonical: "https://app.horyon.xyz/map" },
  openGraph: {
    type: "website",
    title: "Atlas · Horyon",
    description: _MAP_DESC,
    url: "https://app.horyon.xyz/map",
  },
};

// The graph aggregation reads the precomputed entity_edges (rebuilt by the bot's
// entity_graph cron every 6h). A 1h TTL keeps the map well within that cadence
// while sparing the DB the per-request top-N + edge join. `unstable_cache` applies
// the revalidate even though the root layout is `force-dynamic`.
// A tighter top-N (was 260) keeps the map a curated, legible core rather than a
// cloud of weakly-connected dust — and lightens the client layout cost.
const loadGraph = unstable_cache(
  () => getEntityGraph({ maxNodes: 170, minWeight: 2 }),
  ["entity-graph-v2"],
  { revalidate: 3600, tags: ["entity-graph"] }
);

// The Index screener ranks ALL tracked entities by coverage, with DeFiLlama TVL
// aggregated to the brand where it exists. Same 1h TTL as the graph — both ride the
// 2h DeFiLlama + 6h entity_graph crons, so the cache is always well inside cadence.
const loadLeague = unstable_cache(
  () => getEntityLeague({ limit: 200 }),
  ["entity-league-v3"],
  { revalidate: 3600, tags: ["entity-graph"] }
);

export default async function EntityMapPage() {
  let graph = { nodes: [], edges: [] };
  let league = [];
  try { [graph, league] = await Promise.all([loadGraph(), loadLeague()]); }
  catch { /* empty graph */ }

  return (
    <article className="digest">
      <EntityGraph nodes={graph.nodes} edges={graph.edges} league={league} />
    </article>
  );
}
