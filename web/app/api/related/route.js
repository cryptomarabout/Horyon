import { pool } from "../../../lib/db";
import { parseDigest } from "../../../lib/digest";

// Words too generic to use as entity signals
const STOP = new Set([
  "bitcoin","ethereum","crypto","blockchain","launches","announced","announces",
  "market","protocol","network","token","tokens","update","upgrades","report",
  "news","week","month","daily","latest","platform","users","address","addresses",
  "total","value","locked","million","billion","dollar","dollars","percent",
  "proposal","governance","community","foundation","decentralized","layer",
  "exchange","trading","volume","price","fund","funds","invest","investor",
  "hack","exploit","attack","vulnerability","security","audit",
]);

// Extract meaningful content words from a title string
function extractTerms(title) {
  return (title || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(w => w.length >= 4 && !STOP.has(w));
}

export async function POST(request) {
  try {
    const { protocols = [], chains = [], title = "" } = await request.json();

    // Protocol/chain names are the primary entity signal (exact, reliable)
    const exactNames = [...protocols, ...chains]
      .map(n => n.toLowerCase())
      .filter(n => n.length >= 3);

    // Title keywords as a fallback when no protocol/chain matches exist
    const terms = exactNames.length ? [] : extractTerms(title);

    if (!exactNames.length && !terms.length) {
      return Response.json({ articles: [] });
    }

    // Fetch last 30 days of digests, one row per date (latest generated)
    const { rows } = await pool.query(
      `SELECT DISTINCT ON (date)
          to_char(date,'YYYY-MM-DD') AS date,
          content
       FROM crypto_digest
       WHERE date >= CURRENT_DATE - 30
       ORDER BY date DESC, created_at DESC`
    );

    const articles = [];
    const seenTitles = new Set();
    if (title) seenTitles.add(title.toLowerCase());

    for (const row of rows) {
      const { bullets } = parseDigest(row.content);
      for (const b of bullets) {
        const bt = (b.title || "").toLowerCase();
        if (!bt || seenTitles.has(bt)) continue;

        const combined = bt + " " + (b.body || "").toLowerCase();
        const hasExact = exactNames.some(n => combined.includes(n));
        const hasTerm  = !hasExact && terms.some(t => combined.includes(t));

        if (hasExact || hasTerm) {
          seenTitles.add(bt);
          articles.push({
            date:  row.date,
            title: b.title,
            body:  b.body  || null,
            link:  b.link  || null,
            score: hasExact ? 2 : 1,
          });
        }
      }
    }

    // Exact entity matches first, then by date descending
    articles.sort((a, b) => {
      if (a.score !== b.score) return b.score - a.score;
      return b.date.localeCompare(a.date);
    });

    return Response.json({ articles: articles.slice(0, 6) });
  } catch {
    return Response.json({ articles: [] });
  }
}
