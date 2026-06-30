import { NextResponse } from "next/server";
import { pool } from "../../../lib/db";

// PUBLIC TYPEAHEAD — entity suggestions for the free-text search bar.
// The whole entity universe is tiny (~3.8k rows), so we build an in-memory index ONCE
// and refresh it lazily on a TTL. Every keystroke is then an in-process filter: zero DB
// round-trip, zero external call, sub-millisecond — it scales to arbitrary QPS and
// can't be turned into a per-request DoS the way a live query/embed could.
//
// Suggestions are ranked so the happy path stays "click a known entity → precomputed
// brief": a known entity (prefix match, has a brief, high mention_count) floats to the
// top; selecting one routes through /api/search which resolves the brief first.

const TTL_MS = 5 * 60 * 1000; // rebuild the index at most every 5 minutes
const MAX_SUGGESTIONS = 8;

let CACHE = null;          // { entries, built }
let BUILDING = null;       // in-flight rebuild promise (prevents thundering herd)

// Mirrors the runtime/web stop-list intent: a bare alias that is also generic vocab is a
// false-positive generator and must never be offered as a standalone suggestion.
const STOPWORDS = new Set([
  "chain","free","idle","defi","token","tokens","network","protocol","finance","open",
  "world","new","core","main","node","fund","funds","labs","across","yield","capital",
  "basis","group","standard","bridge","native","wrapped","push","vault","vaults","credit",
  "lending","stable","stablecoin","savings","saving","staking","staked","liquid",
  "restaking","perp","perps","pool","pools","prime","real","spot","treasury","rewards",
  "points","public","swap","swaps","dex","blockchain","digital","crypto","decentralized",
  "global","circle","fun","current","team","reserve","extra","neutral","funding","story",
  "general","future","futures","signal","simple","instant","secure","trust","official",
  "select","fixed","smart","auto","strategy","movement","bullish","bearish","believe",
  "master","super","summit","pure","solid","basic","fair","grand","markets","market",
]);

async function buildIndex() {
  // Entities worth surfacing: real protocols/chains/daos/people, not news outlets/tools.
  const { rows: ents } = await pool.query(
    `SELECT name, type, mention_count, aliases
     FROM entity_memory
     WHERE mention_count >= 2 AND type NOT IN ('other')`
  );
  // Which canonical names have a precomputed brief (the strongest answer we can serve).
  const { rows: briefed } = await pool.query(
    `SELECT DISTINCT lower(entity_name) AS n FROM entity_intel_brief`
  );
  const hasBrief = new Set(briefed.map(r => r.n));

  // Build a map: alias_lname → highest mention_count among entities that claim it as an alias.
  // Used to detect "shadowed" entities: those whose canonical name is merely an alias of a
  // more-prominent entity (e.g. "ondo global markets" is an alias of "ondo finance" with 61
  // mentions, so the separate "Ondo Global Markets" row with 11 mentions is shadowed).
  const aliasOwner = new Map();
  for (const e of ents) {
    const mc = e.mention_count || 0;
    for (const a of (e.aliases || [])) {
      const al = String(a || "").trim().toLowerCase();
      if (!al) continue;
      if (mc > (aliasOwner.get(al) ?? 0)) aliasOwner.set(al, mc);
    }
  }

  const entries = ents.map(e => {
    const lname = e.name.toLowerCase();
    // Collect display-worthy alternative match keys (aliases), minus noise.
    const aliasKeys = (e.aliases || [])
      .map(a => String(a || "").trim())
      .filter(a => a.length >= 4 && !a.startsWith("@") && !STOPWORDS.has(a.toLowerCase()));
    // Shadowed: a more-prominent entity (higher mention_count) claims this entity's canonical
    // name as one of its aliases, making this row a sub-product / variant already covered.
    const ownerMc = aliasOwner.get(lname) ?? 0;
    return {
      name: e.name,
      lname,
      type: e.type,
      mentions: e.mention_count || 0,
      hasBrief: hasBrief.has(lname),
      aliasKeys: aliasKeys.map(a => a.toLowerCase()),
      shadowed: ownerMc > (e.mention_count || 0),
    };
  });
  return { entries, built: Date.now() };
}

async function getIndex() {
  if (CACHE && Date.now() - CACHE.built < TTL_MS) return CACHE;
  if (!BUILDING) {
    BUILDING = buildIndex()
      .then(idx => { CACHE = idx; return idx; })
      .finally(() => { BUILDING = null; });
  }
  // If a stale cache exists, serve it rather than block on the rebuild.
  if (CACHE) return CACHE;
  return BUILDING;
}

// Collapse versioned / sub-product variants to a single family so the dropdown never
// shows "Aave", "Aave V2", "Aave V3" side by side. We strip trailing version markers
// (v2, v3, "version 3", bare "3", ".5") to derive the family base; the bare parent name
// is the representative when present, otherwise the best-scoring variant stands in.
function familyKey(lname) {
  let s = lname;
  let prev;
  do {
    prev = s;
    s = s.replace(/\s+(?:v|ver|version)?\s*\d+(?:\.\d+)*$/i, "").trim();
  } while (s !== prev && s.length > 0);
  return s || lname;
}

// Score a single entry against the lowercased query. Higher = better; 0 = no match.
function score(entry, q) {
  let best = 0;
  if (entry.lname.startsWith(q)) best = 100;
  else if (entry.lname.includes(q)) best = 60;
  for (const a of entry.aliasKeys) {
    if (a.startsWith(q)) best = Math.max(best, 80);
    else if (a.includes(q)) best = Math.max(best, 45);
  }
  if (!best) return 0;
  // Tie-breakers: a servable brief, then how often the entity actually shows up.
  return best + (entry.hasBrief ? 15 : 0) + Math.min(entry.mentions, 200) / 20;
}

export async function GET(req) {
  const q = (new URL(req.url).searchParams.get("q") || "").trim().toLowerCase();
  if (q.length < 2) return NextResponse.json({ suggestions: [] });

  let index;
  try {
    index = await getIndex();
  } catch (e) {
    console.error("[suggest] index build failed:", e?.message ?? e);
    return NextResponse.json({ suggestions: [] });
  }

  const scored = [];
  for (const e of index.entries) {
    const s = score(e, q);
    if (s > 0) scored.push({ e, s });
  }
  scored.sort((a, b) => b.s - a.s);

  // Keep one representative per family. Walking in score order, the first variant we see
  // claims the family; a later bare-parent match (name === family base) is allowed to
  // replace a versioned representative so "Aave" wins over "Aave V3".
  const byFamily = new Map();
  const order = [];
  for (const { e, s } of scored) {
    const fam = familyKey(e.lname);
    const cur = byFamily.get(fam);
    if (!cur) {
      byFamily.set(fam, { e, s });
      order.push(fam);
    } else if (e.lname === fam && cur.e.lname !== fam) {
      byFamily.set(fam, { e, s }); // prefer the bare parent as the representative
    }
  }

  // Second-pass: drop shadowed entries whose canonical name is covered by a non-shadowed
  // representative already in the result set. Build the union of aliasKeys from non-shadowed
  // reps, then filter out any shadowed rep whose lname is in that set.
  // This collapses sub-products / variant rows that shouldn't appear alongside their parent
  // (e.g. a separate "Ondo Global Markets" entity disappears when "Ondo Finance" is present
  // and lists "ondo global markets" as one of its own aliases).
  const nonShadowedAliasKeys = new Set(
    order
      .map(fam => byFamily.get(fam).e)
      .filter(e => !e.shadowed)
      .flatMap(e => e.aliasKeys)
  );
  const deduped = order.filter(fam => {
    const e = byFamily.get(fam).e;
    return !(e.shadowed && nonShadowedAliasKeys.has(e.lname));
  });

  // Third-pass: when the query exactly matches the first word of multiple surviving entries
  // (e.g. "aave" → "Aave" + "Aave Horizon", "ondo" → "Ondo Finance" + "Ondo Perps"),
  // keep only the highest-scoring one. Walking in score order means the first hit wins.
  // This avoids flooding the dropdown with brand sub-products when the parent is already
  // present. A more specific query ("aave horizon", "ondo p") bypasses this naturally
  // since q then differs from the first word of each entry.
  let firstWordSlotTaken = false;
  const collapsed = deduped.filter(fam => {
    const e = byFamily.get(fam).e;
    const fw = e.lname.indexOf(" ") === -1 ? e.lname : e.lname.slice(0, e.lname.indexOf(" "));
    if (fw !== q) return true;
    if (!firstWordSlotTaken) { firstWordSlotTaken = true; return true; }
    return false;
  });

  const suggestions = collapsed
    .slice(0, MAX_SUGGESTIONS)
    .map(fam => byFamily.get(fam).e)
    .map(e => ({ name: e.name, type: e.type, hasBrief: e.hasBrief }));
  return NextResponse.json({ suggestions });
}
