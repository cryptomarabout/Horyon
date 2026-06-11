import { NextResponse } from "next/server";
import { pool, getEntityIntelBrief } from "../../../lib/db";
import { chatCreate } from "../../../lib/llm";

const OLLAMA_HOST = process.env.OLLAMA_HOST || "http://host.docker.internal:11434";
const EMBED_MODEL = process.env.EMBED_MODEL || "nomic-embed-text";
const TOPK        = 15;
const DAYS        = 30;
const PROBES      = 10;
const MAX_STEPS   = 8;

// Exact mirror of app/prompts.py SPECIALIZED_SYSTEM
const SYSTEM = `You are a crypto-native analyst, deep in DeFi since 2020. Direct, no fluff, opinionated edge. You know signal from noise.

TOOL USE — MANDATORY:
- You MUST call search_feed MULTIPLE TIMES before answering (minimum 3 calls).
- ONE keyword per call only. Never combine protocols.
- BAD: "Aave Ethena" → GOOD: "aave" then "ethena" then "base"
- If follow-up: check conversation history first, then re-query only if needed.
- NEVER answer before completing all searches.

HARD DISCARD — never mention:
- ETF, futures, institutional, BlackRock, Fidelity, MicroStrategy
- SEC, regulation, law, ban, legal
- Price prediction, TA, ATH, bull/bear market
- Memecoin, shitcoin, pump
- Solana (except confirmed hack)
- Quantum computing, sponsored content

INCLUDE ONLY:
- Protocol launches, upgrades, integrations
- New Dapps, L2s, bridges, restaking
- Governance votes with onchain consequence
- Liquidity moves, yield shifts, onchain activity
- Confirmed hacks 🚨
- Technical DeFi narratives

TIME FILTER: only items from the last 30 days.

OUTPUT FORMAT (Telegram HTML only — no markdown):
- Keyword/update request:
  🔎 <b>{keyword}</b>

  • <b>Short title</b> — What happened. Why it matters. <a href="url">🔗</a>
  (5–10 bullets, each with a link)

- Question:
  ❓ <b>{question}</b>

  Direct answer (1–3 sentences).

  • <b>Supporting item</b> — relevance <a href="url">🔗</a>

- Follow-up: answer from history, add bullets only if useful.

RULES:
- GROUNDING: Base every bullet ONLY on search_feed results (or the conversation history). Use only links that appear verbatim in those results. Never invent items, numbers, dates, or URLs from memory. If the searches return nothing relevant, say so plainly — do not fabricate.
- Every bullet must have a link
- No **, no \`\`\` — only <b> and <a> tags
- No padding, no intro, no disclaimers`;

const SEARCH_TOOL = {
  type: "function",
  function: {
    name: "search_feed",
    description:
      "Search the crypto feed database semantically. Call multiple times, ONE keyword per call " +
      "(e.g. 'aave', then 'aave governance', then 'restaking'). Returns the most relevant feed items from the last 30 days.",
    parameters: {
      type: "object",
      properties: {
        keyword: {
          type: "string",
          description: "A single keyword/protocol/topic — never combined with another",
        },
      },
      required: ["keyword"],
    },
  },
};

// ── Embedding + vector search ─────────────────────────────────────────────
async function embedKeyword(kw) {
  const r = await fetch(`${OLLAMA_HOST}/api/embeddings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: EMBED_MODEL, prompt: kw }),
    signal: AbortSignal.timeout(10_000),
  });
  if (!r.ok) throw new Error(`Ollama embed failed: ${r.status}`);
  const { embedding } = await r.json();
  if (!Array.isArray(embedding) || !embedding.length) throw new Error("empty embedding");
  return embedding;
}

async function searchFeedRows(kw) {
  const vec    = await embedKeyword(kw);
  const vecStr = `[${vec.join(",")}]`;
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    // SET LOCAL does not accept $N params — PROBES is a known safe integer constant
    await client.query(`SET LOCAL ivfflat.probes = ${PROBES}`);
    const { rows } = await client.query(
      `SELECT content, link, creator, pub_date, source_type
       FROM feed_items
       WHERE embedding IS NOT NULL
         AND COALESCE(pub_date, ingested_at) >= now() - ($2 * INTERVAL '1 day')
       ORDER BY embedding <=> $1::vector
       LIMIT $3`,
      [vecStr, DAYS, TOPK]
    );
    await client.query("COMMIT");
    return rows;
  } finally {
    client.release();
  }
}

function formatFeedRows(rows) {
  if (!rows.length) return "No matching feed items in the last 30 days.";
  return rows
    .map(r => {
      const when = r.pub_date ? String(r.pub_date).slice(0, 10) : "?";
      const text = (r.content || "")
        .replace(/<[^>]*>/g, " ")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 400);
      return `[${r.source_type || "feed"}] ${r.creator || ""} (${when})\n${text}\nLINK: ${r.link || ""}`;
    })
    .join("\n\n---\n\n");
}

async function runSearchFeedTool(keyword) {
  const kw = (keyword || "").trim();
  if (!kw) return "error: empty keyword";
  try {
    const rows = await searchFeedRows(kw);
    return formatFeedRows(rows);
  } catch (e) {
    console.error("[search] search_feed tool error:", e.message);
    return `error: ${e.message}`;
  }
}

// ── Context builders (mirror of entities.py + analyst.py) ─────────────────

async function buildEntityContext(keyword) {
  try {
    const like = `%${keyword.toLowerCase()}%`;
    const { rows: entities } = await pool.query(
      `SELECT slug, name, type, aliases, summary
       FROM entity_memory
       WHERE lower(name) LIKE $1
          OR lower(slug) LIKE $1
          OR EXISTS (SELECT 1 FROM unnest(aliases) AS a WHERE lower(a) LIKE $1)
       LIMIT 8`,
      [like]
    );
    if (!entities.length) return "";

    const slugs = entities.map(e => e.slug);
    const { rows: protocols } = await pool.query(
      `SELECT slug, tvl_usd, tvl_change_1d, category
       FROM defillama_protocols
       WHERE slug = ANY($1)`,
      [slugs]
    );
    const tvlMap = Object.fromEntries(protocols.map(p => [p.slug, p]));

    const lines = entities.map(e => {
      const parts = [e.name];
      const tvl = tvlMap[e.slug];
      if (tvl?.tvl_usd) {
        const usd = parseFloat(tvl.tvl_usd);
        const fmt = usd >= 1e9 ? `$${(usd / 1e9).toFixed(1)}B`
          : usd >= 1e6 ? `$${(usd / 1e6).toFixed(0)}M`
          : `$${usd.toLocaleString()}`;
        const chg = tvl.tvl_change_1d != null
          ? ` (${parseFloat(tvl.tvl_change_1d) > 0 ? "+" : ""}${parseFloat(tvl.tvl_change_1d).toFixed(1)}% 1d)`
          : "";
        parts.push(`TVL ${fmt}${chg}`);
        if (tvl.category) parts.push(tvl.category);
      }
      if (e.summary) parts.push(e.summary.trim());
      return "  " + parts.join(" | ");
    });

    return "ENTITY CONTEXT (auto-detected from query):\n" + lines.join("\n");
  } catch (e) {
    console.error("[search] entity context error:", e.message);
    return "";
  }
}

async function buildAnalystNotes() {
  try {
    const { rows } = await pool.query(
      `SELECT date, notes FROM analyst_notes
       WHERE date >= CURRENT_DATE - 7
       ORDER BY date DESC
       LIMIT 5`
    );
    if (!rows.length) return "";
    const parts = rows.map(r => `[${String(r.date).slice(0, 10)}]\n${r.notes}`);
    return "ANALYST NOTES — ongoing themes (last 7 days):\n" + parts.join("\n\n");
  } catch (e) {
    console.error("[search] analyst notes error:", e.message);
    return "";
  }
}

// ── Main handler ──────────────────────────────────────────────────────────
export async function POST(req) {
  let kw;
  try {
    const body = await req.json();
    kw = (body?.keyword || "").trim();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (!kw) return NextResponse.json({ error: "keyword required" }, { status: 400 });

  // Fast path: return pre-computed entity brief if one exists (updated within last 7 days)
  const cachedBrief = await getEntityIntelBrief(kw);
  if (cachedBrief?.brief_html) {
    return NextResponse.json({ content: cachedBrief.brief_html, sources: 0 });
  }

  // Build context blocks in parallel (fail silently if tables are missing)
  const [entityCtx, analystNotes] = await Promise.all([
    buildEntityContext(kw),
    buildAnalystNotes(),
  ]);

  let system = SYSTEM;
  const ctxParts = [entityCtx, analystNotes].filter(Boolean);
  if (ctxParts.length) system += "\n\n" + ctxParts.join("\n\n");

  const messages = [
    { role: "system", content: system },
    { role: "user",   content: kw },
  ];

  let searchCallCount = 0;

  try {
    for (let step = 0; step < MAX_STEPS; step++) {
      const useTools = step < MAX_STEPS - 1;
      const { resp } = await chatCreate({
        max_tokens: 800,
        messages,
        ...(useTools ? { tools: [SEARCH_TOOL], tool_choice: "auto" } : {}),
      });

      const msg = resp.choices[0].message;

      // No tool calls → final answer
      if (!msg.tool_calls || msg.tool_calls.length === 0) {
        const content = (msg.content || "").trim();
        return NextResponse.json({ content, sources: searchCallCount * TOPK });
      }

      // Append assistant turn with tool_calls
      messages.push({
        role: "assistant",
        content: msg.content || "",
        tool_calls: msg.tool_calls.map(tc => ({
          id: tc.id,
          type: "function",
          function: { name: tc.function.name, arguments: tc.function.arguments },
        })),
      });

      // Execute each tool call
      for (const tc of msg.tool_calls) {
        let args = {};
        try { args = JSON.parse(tc.function.arguments || "{}"); } catch {}

        let output;
        if (tc.function.name === "search_feed") {
          searchCallCount++;
          output = await runSearchFeedTool(args.keyword || "");
        } else {
          output = `unknown tool: ${tc.function.name}`;
        }

        messages.push({ role: "tool", tool_call_id: tc.id, content: output });
      }
    }

    return NextResponse.json({ error: "Analysis timed out after too many steps." }, { status: 504 });
  } catch (e) {
    console.error("[search] agent error:", e?.message ?? e);
    return NextResponse.json({ error: "AI analysis failed." }, { status: 502 });
  }
}
