// app/lib/db/podcasts.js — summarized podcast episodes
import { unstable_cache } from "next/cache";
import { safeRows, iso } from "./_core.js";


// Recent summarized podcast episodes for the Podcasts sidebar section + panel.
// `analysis` is JSONB → already a parsed object via node-postgres.
export async function getRecentPodcasts(limit = 40) {
  const rows = await safeRows(
    `SELECT video_id, channel, title, url, published_at, analysis
     FROM podcast_episodes
     WHERE status = 'summarized' AND analysis IS NOT NULL
     ORDER BY published_at DESC NULLS LAST
     LIMIT $1`,
    [limit]
  );
  return rows.map(r => ({ ...r, published_at: iso(r.published_at) }));
}


// Summarized podcast episodes published on a specific UTC date — for inline
// "podcasts as daily news" rows in the daily feed. Same shape as getRecentPodcasts.
// Cached 30 min per date — episodes for a past date don't change.
export function getPodcastsForDate(date) {
  return unstable_cache(
    async () => {
      const rows = await safeRows(
        `SELECT video_id, channel, title, url, published_at, analysis
         FROM podcast_episodes
         WHERE status = 'summarized' AND analysis IS NOT NULL
           AND published_at IS NOT NULL
           AND (published_at AT TIME ZONE 'UTC')::date = $1::date
         ORDER BY published_at DESC`,
        [date]
      );
      return rows.map(r => ({ ...r, published_at: iso(r.published_at) }));
    },
    ["horyon-podcasts-date", date],
    { revalidate: 1800 }
  )();
}
