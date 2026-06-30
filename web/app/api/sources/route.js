import { getBulletSourceItems } from "../../../lib/db";

// Words too generic to use as entity signals (mirrors api/related STOP).
const STOP = new Set([
  "bitcoin","ethereum","crypto","blockchain","launches","announced","announces",
  "market","protocol","network","token","tokens","update","upgrades","report",
  "news","week","month","daily","latest","platform","users","address","addresses",
  "total","value","locked","million","billion","dollar","dollars","percent",
  "proposal","governance","community","foundation","decentralized","layer",
  "exchange","trading","volume","price","fund","funds","invest","investor",
  "hack","exploit","attack","vulnerability","security","audit",
]);

function extractTerms(title) {
  return (title || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(w => w.length >= 4 && !STOP.has(w));
}

function getDomain(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return null; }
}

// All sources that reported the same story as a digest bullet. Matches feed items
// on the bullet's entity terms (protocol/chain/entity names; title keywords as a
// fallback) within the digest day's 24h window, then collapses to one row per
// domain — the same population behind the bullet's `source_count` badge.
export async function POST(request) {
  try {
    const { title = "", body = "", date, protocols = [], chains = [], entities = [], primaryLink } = await request.json();
    if (!date) return Response.json({ sources: [] });

    // Entity names are the strongest signal; fall back to title keywords.
    const named = [...protocols, ...chains, ...entities]
      .map(n => (n || "").trim())
      .filter(n => n.length >= 3);
    const terms = named.length ? named : extractTerms(title);
    if (!terms.length) return Response.json({ sources: [] });

    const items = await getBulletSourceItems(terms, date);

    // Collapse to one source per domain — earliest item wins (it set the story).
    const byDomain = new Map();
    for (const it of items) {
      const domain = getDomain(it.link);
      if (!domain) continue;
      if (!byDomain.has(domain)) {
        byDomain.set(domain, {
          domain,
          name: it.creator?.trim() || domain,
          link: it.link,
          type: it.source_type,
          ts: it.ts,
          isPrimary: primaryLink ? it.link === primaryLink : false,
        });
      } else if (primaryLink && it.link === primaryLink) {
        byDomain.get(domain).isPrimary = true;
      }
    }

    // Primary (cited) source first, then chronological.
    const sources = [...byDomain.values()].sort((a, b) => {
      if (a.isPrimary !== b.isPrimary) return a.isPrimary ? -1 : 1;
      return (a.ts || "").localeCompare(b.ts || "");
    });

    return Response.json({ sources });
  } catch {
    return Response.json({ sources: [] });
  }
}
