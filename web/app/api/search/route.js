import { NextResponse } from "next/server";
import { pool, getEntityIntelBrief, AMBIGUOUS_TERMS, CRYPTO_CTX } from "../../../lib/db";
import { escapeHtml, safeHref } from "../../../lib/sanitize.mjs";

// PUBLIC SITE — ZERO PAID-LLM EGRESS, ZERO PER-REQUEST EXTERNAL CALL.
// This route used to run an 8-step ReAct agent (free-text) + a one-shot synth call
// (entity clicks) against the paid NIM/OpenRouter chain. On a public, unauthenticated
// site that is an unbounded cost-DoS. The intelligence is now PRECOMPUTED + PURE-SQL:
//   - entity answers  → entity_intel_brief (built post-digest for every entity in a
//                        bullet, see app/entity_brief.py); cache miss → deterministic
//                        feed render (formatFeedBullets), NEVER a live LLM call.
//   - free-text query → precomputed brief if the query resolves to an entity, else a
//                        Postgres full-text search (feed_items.content_tsv GIN index)
//                        rendered deterministically. This replaced the old per-query
//                        Ollama embedding: FTS is fully in-DB so it scales to high QPS
//                        with no single-host external dependency on the hot path.
// Typeahead entity suggestions are served separately by /api/suggest.
const TOPK = 15;
const DAYS = 30;

// ── Full-text search (pure in-DB, no external call) ───────────────────────
// websearch_to_tsquery handles quotes / OR / - operators and never throws on junk
// input; an all-stopword query yields an empty tsquery that matches nothing, which the
// caller treats as a cache miss and falls back to entityFeedRows (word-boundary match).
// Ranking blends ts_rank (lexical relevance) with a gentle recency decay so a strong but
// week-old hit can still out-rank a marginal fresh one — the "intel feed" feel.
async function searchFeedRows(kw) {
  const { rows } = await pool.query(
    `SELECT content, link, creator, pub_date, source_type
     FROM feed_items, websearch_to_tsquery('english', $1) AS q
     WHERE content_tsv @@ q
       AND COALESCE(pub_date, ingested_at) >= now() - ($2 * INTERVAL '1 day')
     ORDER BY ts_rank(content_tsv, q)
              / (1.0 + EXTRACT(EPOCH FROM (now() - COALESCE(pub_date, ingested_at))) / 86400.0 / 14.0)
              DESC
     LIMIT $3`,
    [kw, DAYS, TOPK]
  );
  return rows;
}

// AMBIGUOUS_TERMS + CRYPTO_CTX (the entity-name false-positive guard) are shared with the
// entity-map "Latest mentions" panel, so they live in lib/db (the entities module, imported above).

// Recent feed items that actually MENTION the entity (word-boundary on its most
// distinctive token), newest first. Precise + fast — unlike pure vector recall, which
// surfaced irrelevant "GM 🟢" noise for short entity names like "Venus Core Pool".
async function entityFeedRows(kw) {
  const token = (kw.split(/\s+/)
    .map(w => w.replace(/[^a-z0-9.+]/gi, ""))
    .filter(w => w.length >= 3)
    .sort((a, b) => b.length - a.length)[0]) || "";
  if (!token) return [];
  const esc = token.replace(/\./g, "[.]").replace(/\+/g, "[+]");
  const ambiguous = AMBIGUOUS_TERMS.has(token.toLowerCase());
  const { rows } = await pool.query(
    `SELECT content, link, creator, pub_date, source_type
     FROM feed_items
     WHERE COALESCE(pub_date, ingested_at) >= now() - INTERVAL '30 days'
       AND content ~* ('\\y' || $1 || '\\y')
       ${ambiguous ? "AND content ~* $2" : ""}
     ORDER BY COALESCE(pub_date, ingested_at) DESC
     LIMIT 8`,
    ambiguous ? [esc, CRYPTO_CTX] : [esc]
  );
  return rows;
}

// Deterministic, NO-LLM rendering of the top feed matches as Telegram-HTML bullets —
// the universal fallback for any cache miss. Returns in ~100ms instead of a live LLM call.
function formatFeedBullets(rows, kw) {
  const seen = new Set();
  const bullets = [];
  for (const r of rows) {
    if (r.link && seen.has(r.link)) continue;
    if (r.link) seen.add(r.link);
    const text = (r.content || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    if (!text) continue;
    const head = (r.creator || r.source_type || "feed").trim();
    const href = safeHref(r.link);
    const link = href ? ` <a href="${href}">🔗</a>` : "";
    bullets.push(`• <b>${escapeHtml(head)}</b> — ${escapeHtml(text.slice(0, 180))}${link}`);
    if (bullets.length >= 7) break;
  }
  if (!bullets.length) {
    return `🔎 <b>${escapeHtml(kw)}</b>\n\nNo recent feed items in the last 30 days.`;
  }
  return `🔎 <b>${escapeHtml(kw)}</b>\n\n${bullets.join("\n")}`;
}

// Deterministic feed render for the free-text bar: Postgres FTS (content_tsv), with a
// word-boundary fallback when the query reduces to an empty tsquery (all stopwords) or
// FTS returns nothing.
async function feedAnswer(kw) {
  let rows = [];
  try {
    rows = await searchFeedRows(kw);
  } catch (e) {
    console.error("[search] FTS failed, falling back to text match:", e?.message ?? e);
  }
  if (!rows.length) rows = await entityFeedRows(kw);
  return formatFeedBullets(rows, kw);
}

// ── Main handler ──────────────────────────────────────────────────────────
export async function POST(req) {
  let kw, entityMode, mode;
  try {
    const body = await req.json();
    kw = (body?.keyword || "").trim();
    entityMode = !!body?.entity;   // entity-tag click vs free-text search-bar query
    mode = body?.mode || "feed";   // entity mode only: "feed" (fast) | "synth" (brief)
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (!kw) return NextResponse.json({ error: "keyword required" }, { status: 400 });
  if (kw.length > 120) kw = kw.slice(0, 120);

  // ── Entity-tag click ────────────────────────────────────────────────────
  // "feed" renders instantly; "synth" returns the precomputed brief if one exists,
  // else the SAME deterministic feed render. Neither phase ever calls an LLM.
  if (entityMode) {
    try {
      if (mode === "synth") {
        const brief = await getEntityIntelBrief(kw);
        if (brief?.brief_html) {
          return NextResponse.json({
            content: brief.brief_html, sources: 0, cached: true, asOf: brief.digest_date,
          });
        }
      }
      const rows = await entityFeedRows(kw);
      return NextResponse.json({ content: formatFeedBullets(rows, kw), sources: rows.length });
    } catch (e) {
      console.error("[search] entity error:", e?.message ?? e);
      return NextResponse.json(
        { content: `🔎 <b>${escapeHtml(kw)}</b>\n\nNo data available right now.`, sources: 0 });
    }
  }

  // ── Free-text search bar ──────────────────────────────────────────────────
  // Precomputed brief if the query resolves to an entity (canonical or alias), else a
  // deterministic feed render. No ReAct agent, no paid LLM.
  try {
    const brief = await getEntityIntelBrief(kw);
    if (brief?.brief_html) {
      return NextResponse.json({ content: brief.brief_html, sources: 0, cached: true, asOf: brief.digest_date });
    }
    return NextResponse.json({ content: await feedAnswer(kw), sources: 0 });
  } catch (e) {
    console.error("[search] free-text error:", e?.message ?? e);
    return NextResponse.json({ error: "Search failed." }, { status: 502 });
  }
}
