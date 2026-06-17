import { unstable_cache } from "next/cache";
import { getEntityGraph } from "../../../lib/db";
import EntityGraph from "../../components/EntityGraph";

export const metadata = {
  title: "Entity Map · Horyon",
};

// The graph aggregation reads the precomputed entity_edges (rebuilt by the bot's
// entity_graph cron every 6h). A 1h TTL keeps the map well within that cadence
// while sparing the DB the per-request top-N + edge join. `unstable_cache` applies
// the revalidate even though the root layout is `force-dynamic`.
const loadGraph = unstable_cache(
  () => getEntityGraph({ maxNodes: 260, minWeight: 2 }),
  ["entity-graph-v1"],
  { revalidate: 3600, tags: ["entity-graph"] }
);

export default async function EntityMapPage() {
  let graph = { nodes: [], edges: [] };
  try { graph = await loadGraph(); } catch { /* empty graph */ }

  return (
    <article className="digest">
      <EntityGraph nodes={graph.nodes} edges={graph.edges} />
    </article>
  );
}
