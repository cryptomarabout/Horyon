import { pool } from "../../../../../lib/db";

export const dynamic = "force-dynamic";

const VARIANTS = new Set(["short", "standard", "explainer"]);

// GET /api/audio/YYYY-MM-DD/transcript?variant=short|standard|explainer
// Returns the spoken script for a ready audio briefing (no auth — same public posture as the
// audio bytes route). Returns 404 when no script exists or the row isn't ready.
export async function GET(req, { params }) {
  const date = params?.date;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "")) {
    return Response.json({ error: "bad date" }, { status: 400 });
  }
  let variant = new URL(req.url).searchParams.get("variant") || "standard";
  if (!VARIANTS.has(variant)) variant = "standard";

  let row;
  try {
    const { rows } = await pool.query(
      `SELECT script, variant, to_char(digest_date,'YYYY-MM-DD') AS date
       FROM digest_audio
       WHERE digest_date = $1::date AND variant = $2
         AND script IS NOT NULL AND script <> '' AND status = 'ready'`,
      [date, variant]
    );
    row = rows[0];
  } catch {
    return Response.json({ error: "unavailable" }, { status: 503 });
  }
  if (!row) return Response.json({ error: "not found" }, { status: 404 });

  return Response.json(
    { date: row.date, variant: row.variant, script: row.script },
    { headers: { "Cache-Control": "public, max-age=3600" } }
  );
}
