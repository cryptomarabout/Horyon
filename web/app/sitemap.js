import { listDigests } from "../lib/db";

// Dynamic sitemap. Built per-request (DB is not available at build time, and the digest
// list grows daily) — force-dynamic keeps Next from trying to evaluate it statically.
export const dynamic = "force-dynamic";

const SITE = "https://app.horyon.xyz";

export default async function sitemap() {
  let digests = [];
  try {
    digests = await listDigests();
  } catch (e) {
    // Never let a transient DB hiccup 500 the sitemap — degrade to the static routes.
    console.error("[sitemap] listDigests failed:", e?.message ?? e);
  }

  const now = new Date();

  // Top-level view routes (sibling surfaces of the top-nav). /threads is intentionally
  // omitted — it is basic-auth gated and disallowed in robots.txt.
  const staticRoutes = ["", "/narratives", "/map", "/weekly"].map((p) => ({
    url: `${SITE}${p}`,
    lastModified: now,
    changeFrequency: p === "" ? "daily" : "weekly",
    priority: p === "" ? 1.0 : 0.6,
  }));

  // One entry per digest date. The most recent digest carries the highest priority and
  // an hourly change hint (it can be regenerated through the day); older ones are frozen.
  const digestRoutes = digests.map((d, i) => ({
    url: `${SITE}/d/${d.date}`,
    lastModified: d.created_at ? new Date(d.created_at + "Z") : now,
    changeFrequency: i === 0 ? "hourly" : "monthly",
    priority: i === 0 ? 0.9 : 0.7,
  }));

  return [...staticRoutes, ...digestRoutes];
}
