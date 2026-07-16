import { unstable_cache } from "next/cache";
import { getProtocolCandidates, getEntityCandidates, getChainDirectory } from "./db";
import { TAG_STOPWORDS } from "./tagStopwords.js";

// Stop-list single-sourced in lib/tagStopwords.js, shared with the SQL matchers in
// lib/db/entities.js — this file carried a hand-synced copy until 2026-07-15.
const STOPWORDS = new Set(TAG_STOPWORDS);

const ENTITY_BRAND_TYPES = new Set(['protocol', 'chain', 'dao', 'exchange', 'fund']);

const SINGLE_WORD_BRAND_RE = /^([A-Z][a-z]+|[A-Z]+)$/;
const TITLECASE_RE = /^[A-Z][a-z]+$/;

// Regex-escapes only '.' and '+' — mirrors the Postgres side's
// replace(replace(name,'.','[.]'),'+','[+]') so behavior stays byte-for-byte identical.
function escapeDotPlus(s) {
  return s.replace(/\./g, "\\.").replace(/\+/g, "\\+");
}

function safeRegex(pattern, flags) {
  try { return new RegExp(pattern, flags); }
  catch { return null; }
}

// ── Protocol matcher (mirrors web/lib/db/entities.js:searchProjectInfo) ────────────
// Precompiled ONCE per candidate row (not per bullet) — this is the whole point of the
// rewrite: the old code re-ran an equivalent regex-per-row SQL scan once PER BULLET
// PER DIGEST (measured ~1.3s/bullet on the entity side alone on the 2-vCPU host — the
// dynamic per-row regex can't use an index since the pattern is built FROM the row).
function compileProtocol(p) {
  const name = p.name || "";
  const lname = name.toLowerCase();
  if (STOPWORDS.has(lname)) {
    p._mainRe = null;
  } else if (SINGLE_WORD_BRAND_RE.test(name)) {
    p._mainRe = safeRegex(`\\b(?:${escapeDotPlus(name)}|${escapeDotPlus(name.toUpperCase())})\\b`);
  } else {
    p._mainRe = safeRegex(`\\b${escapeDotPlus(name)}\\b`, "i");
  }
  p._firstWordRe = null;
  if (name.includes(" ")) {
    const firstWord = name.split(" ")[0];
    if (firstWord.length >= 4 && !STOPWORDS.has(firstWord.toLowerCase())) {
      p._firstWordRe = safeRegex(`\\b${escapeDotPlus(firstWord)}\\b`, "i");
    }
  }
  return p;
}

function protocolMatches(p, text) {
  let matched = (p._mainRe && p._mainRe.test(text)) || (p._firstWordRe && p._firstWordRe.test(text));
  if (matched && p.category === "Canonical Bridge" && !/\bbridge\b/i.test(text)) matched = false;
  return matched;
}

// ── Entity matcher (mirrors web/lib/db/entities.js:searchEntityMemory) ─────────────
function compileEntity(e, parentSet) {
  const name = e.name || "";
  const lname = name.toLowerCase();
  const singleWordBrand = SINGLE_WORD_BRAND_RE.test(name);

  e._aliasRes = (e.aliases || [])
    .filter(a => a.length >= 4 && !a.startsWith("@") && !STOPWORDS.has(a.toLowerCase()))
    .filter(a => !(singleWordBrand && a.toLowerCase() === lname))
    .map(a => safeRegex(`\\b${escapeDotPlus(a)}\\b`, "i"))
    .filter(Boolean);

  e._nameRe = null;
  e._firstWordRe = null;
  if (name.includes(" ")) {
    e._nameRe = safeRegex(`\\b${escapeDotPlus(name)}\\b`, "i");
    const firstWord = name.split(" ")[0];
    if (
      firstWord.length >= 6 &&
      !STOPWORDS.has(firstWord.toLowerCase()) &&
      !parentSet.has(firstWord.toLowerCase())
    ) {
      e._firstWordRe = safeRegex(`\\b${escapeDotPlus(firstWord)}\\b`, "i");
    }
  }

  e._brandRe = null;
  if (singleWordBrand && !STOPWORDS.has(lname)) {
    const isTitleCase = TITLECASE_RE.test(name);
    const lenOk =
      name.length >= 6 ||
      (isTitleCase && name.length >= 4 && name.length <= 5) ||
      (name.length >= 3 && name.length <= 5 && e.mention_count >= 6 && ENTITY_BRAND_TYPES.has(e.type));
    if (lenOk) {
      e._brandRe = safeRegex(`\\b(?:${escapeDotPlus(name)}|${escapeDotPlus(name.toUpperCase())})\\b`);
    }
  }
  return e;
}

function entityMatches(e, text) {
  if (e._aliasRes.some(re => re.test(text))) return true;
  if (e._nameRe && e._nameRe.test(text)) return true;
  if (e._firstWordRe && e._firstWordRe.test(text)) return true;
  if (e._brandRe && e._brandRe.test(text)) return true;
  return false;
}

// Build project hints for every bullet — 100% from our own Postgres, ZERO per-request
// external calls (the web container's zero-egress rule). Protocols/categories/entities
// come from defillama_protocols + entity_memory; the chain roster comes from entity_memory
// (~79 chains) via getChainDirectory, so EVERY tracked chain — not just the 6 we snapshot
// TVL for — gets its icons.llamao.fi chip logo. Live token price is intentionally gone —
// the panel price line self-hides when absent.
//
// Matching happens in JS against candidate rows fetched ONCE for the whole date
// (getProtocolCandidates / getEntityCandidates), each row's regexes precompiled once —
// not per bullet. See web/lib/db/entities.js for why the old per-bullet SQL scan was
// the dominant cost of the daily feed's first paint.
async function _buildProjectHints(bullets) {
  if (!bullets.length) return [];

  let rankedChains = [];
  try {
    const dir = await getChainDirectory();
    rankedChains = dir.map((c, i) => ({ name: c.name, tvl: c.tvl_usd ?? null, rank: i + 1 }));
  } catch {}

  const [protocolRows, entityData] = await Promise.all([
    getProtocolCandidates().catch(() => []),
    getEntityCandidates().catch(() => ({ rows: [], parentNames: [] })),
  ]);

  const parentSet = new Set(entityData.parentNames || []);
  const protocols = (protocolRows || []).map(compileProtocol);
  const entities = (entityData.rows || []).map(e => compileEntity(e, parentSet));

  // Search text: title + first 80 chars of body gives entity signal without bloating the query.
  const searchTexts = bullets.map(b =>
    b.title + (b.body ? " " + b.body.slice(0, 80) : "")
  );

  return bullets.map((b, i) => {
    const text = searchTexts[i];

    const matchedProtocols = protocols
      .filter(p => protocolMatches(p, text))
      .slice(0, 5)
      .map(p => ({
        slug: p.slug, name: p.name, category: p.category, chains: p.chains,
        chain_tvls: p.chain_tvls, tvl_usd: p.tvl_usd, tvl_change_1d: p.tvl_change_1d,
        url: p.url, logo_url: p.logo_url, token_symbol: p.token_symbol, gecko_id: p.gecko_id,
      }));

    const chains = rankedChains
      .filter(c => {
        const name = (c.name || "").trim();
        if (name.length < 3) return false;
        // Word-boundary check prevents "Base" matching "Based" or "Database"
        const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const re = safeRegex(`\\b${escaped}\\b`, "i");
        return re ? re.test(b.title) : false;
      })
      .slice(0, 3)
      .map(c => ({
        name: c.name,
        tvl: c.tvl ?? null,
        rank: c.rank,
        tokenSymbol: null,
        logo: `https://icons.llamao.fi/icons/chains/rsz_${c.name.toLowerCase()}.jpg`,
        url: `https://defillama.com/chain/${encodeURIComponent(c.name)}`,
      }));

    // entity_memory entities: include those not already covered by DeFiLlama protocols.
    // Deduplicate by slug AND by normalised name (strip hyphens/spaces for fuzzy match:
    // "ether-fi" ≈ "etherfi" ≈ "ether fi").
    const normalise = s => s.toLowerCase().replace(/[-.\s]/g, "");
    const defillamaNames = new Set(matchedProtocols.map(p => p.name.toLowerCase()));
    const defillamaSlugNorms = new Set(matchedProtocols.map(p => normalise(p.slug || p.name)));

    const entityTags = entities
      // `entities` is already mention_count-DESC (from getEntityCandidates), so filter()
      // preserves that order — mirrors the original SQL's ORDER BY + LIMIT 6, applied
      // BEFORE the defillama dedup below (a dedup-then-backfill would show a different
      // set than the original always did).
      .filter(e => entityMatches(e, text))
      .slice(0, 6)
      .filter(e =>
        !defillamaNames.has(e.name.toLowerCase()) &&
        !defillamaSlugNorms.has(normalise(e.slug)) &&
        !defillamaSlugNorms.has(normalise(e.name))
      )
      .map(e => {
        const handle = e.twitter_handle?.startsWith("@")
          ? e.twitter_handle.slice(1)
          : e.twitter_handle;
        // Avatar fallback chain — EntityAvatar walks it in order, then draws a monogram
        // if every URL fails, so EVERY entity ends up with an image:
        //   1. /api/avatar/<slug> — the bot-mirrored avatar served from our own DB, but
        //      ONLY when entity_avatars actually has it (avatar_cached). app/avatars.py
        //      resolves the Twitter pic server-side, so the browser NEVER hits unavatar.io
        //      (preserves zero-egress + avoids unavatar's per-client rate-limit). Gating on
        //      the flag avoids a guaranteed-404 request for not-yet-mirrored entities.
        //   2. logo_url = COALESCE(DeFiLlama protocol logo, CoinGecko-seeded entity logo).
        // Mirrors NarrativeView/NarrativePanel.
        const avatars = [
          e.avatar_cached ? `/api/avatar/${e.slug}` : null,
          e.logo_url,
        ].filter(Boolean);
        const url = handle
          ? `https://x.com/${handle}`
          : `https://defillama.com/protocol/${e.slug}`;
        return {
          slug: e.slug,
          name: e.name,
          type: e.type,
          avatars,
          url,
          category: e.category || null,
        };
      });

    return { protocols: matchedProtocols, chains, entityTags };
  });
}

// Cache project hints per digest date — once a digest is published the bullets
// don't change, so 1h TTL is safe and avoids re-matching on every subsequent page
// load for the same date.
export function buildProjectHints(date, bullets) {
  return unstable_cache(
    () => _buildProjectHints(bullets),
    ["horyon-project-hints", date],
    { revalidate: 3600 }
  )();
}
