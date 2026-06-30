// app/lib/db/threads.js — rendered Twitter/X threads
import { pool, safeRows } from "./_core.js";


// ── Twitter/X threads (app/threads.py) — one rendered thread per digest date ──
// Each row is the daily digest rewritten as a ready-to-post thread: a hook tweet
// (carries the /api/og card) + one tweet per bullet, ordered by importance.
export async function listThreadDates() {
  return safeRows(
    `SELECT to_char(digest_date,'YYYY-MM-DD') AS date,
            status,
            jsonb_array_length(tweets) AS tweet_count
     FROM digest_threads
     ORDER BY digest_date DESC`
  );
}


export async function getThread(date) {
  const { rows } = await pool.query(
    `SELECT to_char(digest_date,'YYYY-MM-DD') AS date,
            hook, tweets, cta, og_image_url, model_used, status,
            to_char(created_at,'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
     FROM digest_threads
     WHERE digest_date = $1::date`,
    [date]
  );
  return rows[0] ?? null;
}


export async function latestThreadDate() {
  const { rows } = await pool.query(
    `SELECT to_char(max(digest_date),'YYYY-MM-DD') AS date FROM digest_threads`
  );
  return rows[0]?.date ?? null;
}


// Persist hand-edits from the thread composer. Only the fields supplied are
// written; passing `tweets` replaces the whole array (the editor sends it whole).
export async function updateThread(date, { hook, tweets, cta, status } = {}) {
  const sets = [];
  const vals = [];
  let i = 1;
  if (hook !== undefined)   { sets.push(`hook = $${i++}`);          vals.push(hook); }
  if (tweets !== undefined) { sets.push(`tweets = $${i++}::jsonb`); vals.push(JSON.stringify(tweets)); }
  if (cta !== undefined)    { sets.push(`cta = $${i++}`);           vals.push(cta); }
  if (status !== undefined) { sets.push(`status = $${i++}`);        vals.push(status); }
  if (!sets.length) return null;
  vals.push(date);
  const { rows } = await pool.query(
    `UPDATE digest_threads SET ${sets.join(", ")} WHERE digest_date = $${i}::date
     RETURNING to_char(digest_date,'YYYY-MM-DD') AS date`,
    vals
  );
  return rows[0] ?? null;
}
