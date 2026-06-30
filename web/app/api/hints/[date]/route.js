import { NextResponse } from "next/server";
import { getDigest } from "../../../../lib/db";
import { parseDigest, isValidDigestDate } from "../../../../lib/digest";
import { buildProjectHints } from "../../../../lib/projects";

// Serve pre-computed project hints for a digest date. BulletFeed fetches this
// client-side after bullets render, so the SSR critical path only needs the digest
// content (fast) and hints load asynchronously. buildProjectHints is cached 1h
// per date so warm responses are sub-10ms.
export async function GET(_req, { params }) {
  const { date } = params;
  if (!isValidDigestDate(date)) {
    return NextResponse.json([], { status: 400 });
  }
  try {
    const row = await getDigest(date);
    if (!row) return NextResponse.json([]);
    const { bullets } = parseDigest(row.content);
    const hints = await buildProjectHints(date, bullets);
    return NextResponse.json(hints, {
      headers: { "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=3600" },
    });
  } catch {
    return NextResponse.json([]);
  }
}
