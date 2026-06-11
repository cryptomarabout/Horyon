import { pool } from "../../../lib/db";
import { parseDigest } from "../../../lib/digest";

// Find the digest date that contains a story matching the given title,
// within a weekly date range. Used by the weekly panel's Key Stories links.
export async function POST(request) {
  try {
    const { title, week_start, week_end } = await request.json();
    if (!title || !week_start || !week_end) return Response.json({ date: null });

    const { rows } = await pool.query(
      `SELECT DISTINCT ON (date) to_char(date,'YYYY-MM-DD') AS date, content
       FROM crypto_digest
       WHERE error IS NULL AND date >= $1::date AND date <= $2::date
       ORDER BY date DESC, created_at DESC`,
      [week_start, week_end]
    );

    // Normalize a title: lowercase, strip non-alphanumeric, collapse spaces
    function norm(s) {
      return (s || "").toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
    }

    const needle = norm(title);
    // Use first 20 chars as a fingerprint to allow partial matches
    const prefix = needle.slice(0, 20);

    for (const row of rows) {
      const { bullets } = parseDigest(row.content);
      for (const b of bullets) {
        const hay = norm(b.title);
        if (hay.includes(prefix) || prefix.includes(hay.slice(0, 20))) {
          return Response.json({ date: row.date, body: b.body || null, link: b.link || null });
        }
      }
    }

    // Fallback: return the most recent day in the range
    return Response.json({ date: rows[0]?.date ?? null, body: null, link: null });
  } catch {
    return Response.json({ date: null });
  }
}
