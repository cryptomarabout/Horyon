import { NextResponse } from "next/server";
import { getEntityRecentMentions } from "../../../lib/db";

// PUBLIC SITE — ZERO PAID-LLM EGRESS, PURE SQL.
// Latest feed items that mention a given entity, for the entity-map detail panel. The map
// node carries its own name + slug (from getEntityGraph), so we trust those (regex-escaped
// before the word-boundary match) and never re-resolve from the DB. Returns newest-first
// {link, snippet, source, ts} rows; empty on miss/error.
export async function POST(req) {
  let name, slug;
  try {
    ({ name, slug } = await req.json());
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  name = (name || "").toString().slice(0, 120).trim();
  slug = (slug || "").toString().slice(0, 120).trim();
  if (!name && !slug) {
    return NextResponse.json({ mentions: [] });
  }
  try {
    const mentions = await getEntityRecentMentions(name, slug);
    return NextResponse.json({ mentions });
  } catch (e) {
    console.error("[entity-mentions] error:", e?.message ?? e);
    return NextResponse.json({ mentions: [] });
  }
}
