// app/lib/db/analysis.js — per-bullet analyses, source items, OG card
import { unstable_cache } from "next/cache";
import { safeRows, safeOne, iso } from "./_core.js";


// Pre-computed analyst views + importance scores for all bullets of a digest date.
// Returns { [title]: { analysis, importanceScore, sourceCount } } — empty object if none yet.
// Cached 30 min per date — analyses are written once post-digest and never updated.
export function getBulletAnalyses(date) {
  return unstable_cache(
    async () => {
      const rows = await safeRows(
        `SELECT title, analysis, importance_score, source_count
           FROM digest_bullet_analysis WHERE digest_date = $1::date`,
        [date]
      );
      return Object.fromEntries(rows.map(r => [r.title, {
        analysis:       r.analysis,
        importanceScore: r.importance_score,
        sourceCount:     r.source_count,
      }]));
    },
    ["horyon-bullet-analyses", date],
    { revalidate: 1800 }
  )();
}


// All feed items that corroborate a bullet — i.e. every source that reported the
// same story, not just the one link the digest happened to cite. Mirrors the matcher
// behind the bullet's `source_count` badge (app/db.get_feed_items_matching_terms):
// word-boundary (\y) match of any entity term against feed content, inside the 24h
// window ending at the end of the digest day. Returns rows {link, content, creator,
// source_type, ts} ordered oldest-first; caller dedupes by domain. Empty on miss/error.
export async function getBulletSourceItems(terms, date, windowHours = 24) {
  const parts = [...new Set((terms || []).map(t => (t || "").trim()).filter(t => t.length >= 3))]
    .map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")); // escape regex metachars
  if (!parts.length || !date) return [];
  const pattern = `\\y(${parts.join("|")})\\y`;
  const rows = await safeRows(
    `SELECT link, content, creator, source_type,
            COALESCE(pub_date, ingested_at) AS ts
       FROM feed_items
      WHERE content ~* $1
        AND COALESCE(pub_date, ingested_at) <  $2::date + interval '1 day'
        AND COALESCE(pub_date, ingested_at) >= $2::date + interval '1 day'
                                              - make_interval(hours => $3)
      ORDER BY ts ASC
      LIMIT 200`,
    [pattern, date, windowHours]
  );
  return rows.map(r => ({
    link: r.link,
    content: r.content,
    creator: r.creator,
    source_type: r.source_type,
    ts: iso(r.ts),
  }));
}


// Source publish times for digest bullets, keyed by their source link.
// Bullet links are stored verbatim from feed_items, so an exact-link join resolves
// each bullet's article pub_date. Returns { [link]: ISO-8601 string } — empty on miss/error.
export async function getBulletTimes(links) {
  const uniq = [...new Set((links || []).filter(Boolean))];
  if (!uniq.length) return {};
  const rows = await safeRows(
    `SELECT link, pub_date FROM feed_items
      WHERE link = ANY($1::text[]) AND pub_date IS NOT NULL`,
    [uniq]
  );
  return Object.fromEntries(rows.map(r => [r.link, iso(r.pub_date)]));
}


// Pre-rendered /api/og social card for a date (PNG bytes + mime), or null if not cached.
// The bot renders + stores these post-digest (app/og_cache.py); the og route serves them
// instead of rendering per-request. Read-only — never written from the web container.
export async function getOgCard(date) {
  return safeOne(
    `SELECT png, mime FROM digest_og WHERE digest_date = $1::date`,
    [date]
  );
}
