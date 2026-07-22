// app/lib/db/digest.js — daily digest rows + the connection-cached digest list
import { unstable_cache } from "next/cache";
import { pool } from "./_core.js";


// One entry per day (latest digest for that date), newest first.
// Cached 5 min — list changes once per day after the scheduled digest run.
export const listDigests = unstable_cache(
  async () => {
    const { rows } = await pool.query(
      `SELECT DISTINCT ON (date)
          to_char(date,'YYYY-MM-DD') AS date,
          to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at,
          (length(content) - length(replace(content,'•',''))) AS bullets
       FROM crypto_digest
       ORDER BY date DESC, created_at DESC`
    );
    return rows;
  },
  ["horyon-list-digests"],
  { revalidate: 300 }
);


// Home-page fast path: fetch the latest non-empty digest in a single query,
// avoiding the latestDate() → getDigest() sequential round-trip.
// Cached 5 min — the digest rotates once per day so a short TTL is fine and
// avoids a DB hit on every request to the most-visited page.
export const getLatestDigest = unstable_cache(
  async () => {
    const { rows } = await pool.query(
      `SELECT to_char(date,'YYYY-MM-DD') AS date,
              to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at,
              content
       FROM crypto_digest
       WHERE error IS NULL AND content IS NOT NULL AND content <> ''
       ORDER BY date DESC, created_at DESC
       LIMIT 1`
    );
    return rows[0] ?? null;
  },
  ["horyon-latest-digest"],
  { revalidate: 300 }
);


export async function getDigest(date) {
  const { rows } = await pool.query(
    `SELECT to_char(date,'YYYY-MM-DD') AS date,
            to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at,
            content
     FROM crypto_digest
     WHERE date = $1::date
     ORDER BY created_at DESC
     LIMIT 1`,
    [date]
  );
  return rows[0] ?? null;
}


// Intraday update rows for a date (app/intraday.py — 13:00 + 19:00 UTC), oldest first, so the
// Daily Briefs page can render an "Intraday updates" timeline under the morning brief. Not
// unstable_cache'd: the page is force-dynamic and updates land mid-day, so freshness wins over
// a short stale window (the query is a tiny indexed lookup).
export async function getDigestUpdates(date) {
  const { rows } = await pool.query(
    `SELECT to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at,
            seq, content
     FROM digest_updates
     WHERE date = $1::date AND error IS NULL AND content IS NOT NULL AND content <> ''
     ORDER BY created_at ASC`,
    [date]
  );
  return rows;
}


export async function latestDate() {
  // Skip errored / empty digest rows so the home never lands on a failed run.
  const { rows } = await pool.query(
    `SELECT to_char(max(date),'YYYY-MM-DD') AS date FROM crypto_digest
     WHERE error IS NULL AND content IS NOT NULL AND content <> ''`
  );
  return rows[0]?.date ?? null;
}
