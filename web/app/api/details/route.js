import { NextResponse } from "next/server";
import { pool } from "../../../lib/db";

// PUBLIC SITE — NO LIVE LLM.
// Per-bullet analyst context is precomputed into digest_bullet_analysis post-digest and
// served to the panel via the `cachedAnalysis` prop. This route is the fallback for the
// cases where the prop is absent (e.g. a bullet surfaced outside its digest-day page).
// It now looks the analysis up by title instead of calling the paid LLM chain; on a
// genuine miss it returns empty so the panel shows a graceful "no additional details".
export async function POST(req) {
  let title, body;
  try {
    ({ title, body } = await req.json());
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (!title?.trim()) {
    return NextResponse.json({ error: "title is required" }, { status: 400 });
  }

  try {
    const { rows } = await pool.query(
      `SELECT analysis
         FROM digest_bullet_analysis
        WHERE lower(title) = lower($1)
          AND analysis IS NOT NULL AND analysis <> ''
        ORDER BY digest_date DESC
        LIMIT 1`,
      [title.trim()]
    );
    return NextResponse.json({ content: rows[0]?.analysis || "" });
  } catch (err) {
    console.error("details route error:", err?.message ?? err);
    return NextResponse.json({ content: "" });
  }
}
