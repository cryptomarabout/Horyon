import { pool } from "../../../../lib/db";

export const dynamic = "force-dynamic";

// Length variants stored per date; `?variant=` selects which one to stream (default 'standard').
const VARIANTS = new Set(["short", "standard", "explainer"]);

// GET /api/audio/YYYY-MM-DD?variant=short|standard|explainer — stream the stored audio briefing
// bytes (bytea) for a date + length variant. Range-aware so the <audio> player can seek; only
// 'ready' rows with audio are served. The read site is PUBLIC (since the 2026-06-18 launch) and
// the daily audio briefing is public content by design — same-origin, no auth. Guarded to
// status='ready' so drafts and failed renders never leave the DB.
export async function GET(req, { params }) {
  const date = params?.date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "")) {
    return new Response("bad date", { status: 400 });
  }
  let variant = new URL(req.url).searchParams.get("variant") || "standard";
  if (!VARIANTS.has(variant)) variant = "standard";

  let row;
  try {
    const { rows } = await pool.query(
      `SELECT audio, mime FROM digest_audio
       WHERE digest_date = $1::date AND variant = $2 AND audio IS NOT NULL AND status = 'ready'`,
      [date, variant]
    );
    row = rows[0];
  } catch {
    return new Response("unavailable", { status: 503 });
  }
  if (!row || !row.audio) return new Response("not found", { status: 404 });

  const buf = row.audio; // pg returns bytea as a Node Buffer
  const total = buf.length;
  const mime = row.mime || "audio/mpeg";
  const headers = {
    "Content-Type": mime,
    "Accept-Ranges": "bytes",
    "Cache-Control": "public, max-age=86400, immutable",
  };

  const range = req.headers.get("range");
  if (range) {
    const m = /bytes=(\d*)-(\d*)/.exec(range);
    let start = m && m[1] ? parseInt(m[1], 10) : 0;
    let end = m && m[2] ? parseInt(m[2], 10) : total - 1;
    if (Number.isNaN(start) || Number.isNaN(end) || start > end || start >= total) {
      return new Response("range not satisfiable", {
        status: 416,
        headers: { "Content-Range": `bytes */${total}` },
      });
    }
    end = Math.min(end, total - 1);
    const chunk = buf.subarray(start, end + 1);
    return new Response(chunk, {
      status: 206,
      headers: {
        ...headers,
        "Content-Range": `bytes ${start}-${end}/${total}`,
        "Content-Length": String(chunk.length),
      },
    });
  }

  return new Response(buf, {
    status: 200,
    headers: { ...headers, "Content-Length": String(total) },
  });
}
