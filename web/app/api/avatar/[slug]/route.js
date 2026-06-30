import { pool } from "../../../../lib/db";

export const dynamic = "force-dynamic";

// GET /api/avatar/<slug> — serve a mirrored entity avatar (bytea) for the Entity Map.
// The bot pre-fetches each map entity's avatar into entity_avatars (app/avatars.py); this
// route serves those bytes from our own DB so the browser never stampedes unavatar.io and
// the web container makes ZERO external calls. The map only requests this for entities it
// knows are cached (node.avatarCached); a miss 404s and the client falls back to the
// original logo_url / unavatar URL. Public, same-origin, read-only.
export async function GET(_req, { params }) {
  const slug = params?.slug;
  // Slugs are kebab-case ids; reject anything else before it reaches SQL.
  if (!slug || !/^[a-z0-9][a-z0-9._-]{0,127}$/i.test(slug)) {
    return new Response("bad slug", { status: 400 });
  }

  let row;
  try {
    const { rows } = await pool.query(
      `SELECT image, mime FROM entity_avatars WHERE slug = $1 AND image IS NOT NULL`,
      [slug]
    );
    row = rows[0];
  } catch {
    return new Response("unavailable", { status: 503 });
  }
  if (!row || !row.image) return new Response("not found", { status: 404 });

  const buf = row.image; // pg returns bytea as a Node Buffer
  return new Response(buf, {
    status: 200,
    headers: {
      "Content-Type": row.mime || "image/png",
      "Content-Length": String(buf.length),
      // Avatars change rarely and the bot refreshes the bytes in place; let the
      // browser/CDN hold them for a day.
      "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
    },
  });
}
