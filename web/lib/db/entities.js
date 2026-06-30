// app/lib/db/entities.js — entity matching/search, intel briefs, entity resolution
import { pool, safeRows, iso } from "./_core.js";


// Entity names that are ALSO common English words: matching the bare token floods the
// feed with unrelated articles ("fan base", "growth strategy", "across the board"). For
// these we additionally require a crypto-context signal in the same item so a click on
// "Base" can't surface a soccer headline that merely says "base". Mirrors the false-positive
// class already guarded by GENERIC_TERMS (app/entities.py) and TAG_STOPWORDS — shared here so
// the search route AND the entity-map "Latest mentions" panel match identically.
export const AMBIGUOUS_TERMS = new Set([
  "base", "story", "strategy", "movement", "across", "public", "global", "prime",
  "spot", "standard", "signal", "credit", "future", "futures", "reserve", "stable",
  "secure", "trust", "select", "simple", "instant", "official", "smart", "auto",
  "pure", "master", "super", "grand", "solid", "basic", "fair", "believe", "real",
  "world", "current", "neutral", "extra", "team", "general", "core", "prime",
]);


// Word-boundary crypto signal — broad enough to keep genuine crypto coverage, strict
// enough to drop sports / politics / general-news items for ambiguous-name entities.
export const CRYPTO_CTX =
  "\\y(crypto|cryptocurrency|blockchain|onchain|on-chain|defi|web3|token|tokens|" +
  "chain|network|l2|rollup|mainnet|testnet|protocol|wallet|staking|stake|airdrop|" +
  "tvl|ethereum|eth|bitcoin|btc|solana|sol|stablecoin|coinbase|binance|dao|nft|" +
  "validator|sequencer|liquidity|smart contract)\\y";


// Org-suffix words that aren't a distinctive signal on their own — drop them when picking
// the token to match so "Lido Finance" matches on "lido", not the generic "finance".
const NAME_SUFFIX = new Set([
  "finance", "protocol", "network", "labs", "foundation", "dao", "exchange", "capital",
  "ventures", "fund", "group", "global", "markets", "money", "chain", "io", "xyz", "app",
]);


// Most distinctive token across an entity's name + slug for a word-boundary feed match:
// longest non-generic word, falling back to the longest word overall. Regex-escaped.
function distinctiveToken(name, slug) {
  const words = `${name || ""} ${(slug || "").replace(/-/g, " ")}`
    .toLowerCase()
    .split(/\s+/)
    .map((w) => w.replace(/[^a-z0-9.+]/gi, ""))
    .filter((w) => w.length >= 3);
  if (!words.length) return "";
  const ranked = words.slice().sort((a, b) => b.length - a.length);
  const token = ranked.find((w) => !NAME_SUFFIX.has(w)) || ranked[0];
  return token.replace(/\./g, "[.]").replace(/\+/g, "[+]");
}


// Recent feed items that actually MENTION an entity — word-boundary match on its most
// distinctive token, newest first. Structured for the entity-map detail panel (replaces the
// stale analyst-memory blurb with live coverage). Mirrors entityFeedRows in /api/search but
// returns rows ({link, snippet, source, ts}) instead of HTML bullets. Empty on miss/error.
export async function getEntityRecentMentions(name, slug, { days = 45, limit = 8 } = {}) {
  const token = distinctiveToken(name, slug);
  if (!token) return [];
  const ambiguous = AMBIGUOUS_TERMS.has(token.toLowerCase());
  const rows = await safeRows(
    `SELECT content, link, creator, source_type,
            COALESCE(pub_date, ingested_at) AS ts
       FROM feed_items
      WHERE COALESCE(pub_date, ingested_at) >= now() - ($2 * INTERVAL '1 day')
        AND content ~* ('\\y' || $1 || '\\y')
        ${ambiguous ? "AND content ~* $4" : ""}
      ORDER BY COALESCE(pub_date, ingested_at) DESC
      LIMIT $3`,
    ambiguous ? [token, days, limit, CRYPTO_CTX] : [token, days, limit]
  );
  const seen = new Set();
  const out = [];
  for (const r of rows) {
    if (r.link && seen.has(r.link)) continue;
    if (r.link) seen.add(r.link);
    const text = (r.content || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    if (!text) continue;
    out.push({
      link: r.link || null,
      snippet: text.slice(0, 160),
      source: (r.creator || r.source_type || "").trim() || null,
      ts: iso(r.ts),
    });
  }
  return out;
}


// Pre-computed entity intel brief — returns fresh row (within 7 days) or null.
// Checks by canonical name first, then falls back to alias lookup via entity_memory.
export async function getEntityIntelBrief(query) {
  if (!query) return null;
  // No freshness window: the brief is the ONLY served answer for an entity (the live-LLM
  // fallback is gone), so always return the latest one we have — a brief that is a few
  // weeks old, carrying its digest_date for an "as of" label, beats a live LLM call.
  try {
    const { rows } = await pool.query(
      `SELECT entity_name, brief_html, digest_date
       FROM entity_intel_brief
       WHERE lower(entity_name) = lower($1)
       ORDER BY digest_date DESC
       LIMIT 1`,
      [query]
    );
    if (rows[0]) return rows[0];

    // Alias lookup via entity_memory
    const { rows: em } = await pool.query(
      `SELECT name FROM entity_memory
       WHERE lower(name) = lower($1)
          OR lower($1) = ANY(SELECT lower(a) FROM unnest(aliases) AS a)
       LIMIT 1`,
      [query]
    );
    if (!em[0]) return null;

    const { rows: rows2 } = await pool.query(
      `SELECT entity_name, brief_html, digest_date
       FROM entity_intel_brief
       WHERE lower(entity_name) = lower($1)
       ORDER BY digest_date DESC
       LIMIT 1`,
      [em[0].name]
    );
    return rows2[0] ?? null;
  } catch {
    return null;
  }
}


// Shared stop-list for entity-tag matching: crypto-generic vocab + common English
// words that are ALSO protocol/entity names. Mirrors app/entities.GENERIC_TERMS.
// A bare word-boundary match on any of these is a false-positive generator —
// "Current" on "AAVE's current value", "Team Finance" on any "team", "Funding
// Commons" on any "funding", "Reserve Protocol" on "Chainlink Reserve". Kept in
// ONE place so the DeFiLlama + entity_memory matchers can't drift apart.
const TAG_STOPWORDS = [
  // crypto-generic
  'chain','free','idle','defi','token','tokens','network','protocol','finance',
  'open','world','new','core','main','node','fund','funds','labs','across','yield',
  'capital','basis','group','standard','bridge','native','wrapped','push','vault',
  'vaults','credit','lending','stable','stablecoin','savings','saving','staking',
  'staked','liquid','restaking','perp','perps','pool','pools','prime','real','spot',
  'treasury','rewards','points','public','swap','swaps','dex','blockchain','digital',
  'crypto','decentralized','global','circle','fun',
  // common English words that are also protocol/entity names (false-positive class)
  'current','team','reserve','extra','neutral','funding','story','general','future',
  'futures','signal','simple','instant','secure','trust','official','select','fixed',
  'smart','auto','strategy','movement','bullish','bearish','believe','master','super',
  'summit','pure','solid','basic','fair','grand','markets','market',
  // junk single-word aliases (extraction noise) — NOT brand names (Base/Flow/Spark/
  // Render/Strike are handled by case-sensitive matching, never stop-listed).
  'hard','edge','next','move','link','farm','coin','call','date','deal','news','live',
  'wave','gate',

];
const STOP_SQL = `ARRAY[${TAG_STOPWORDS.map(w => `'${w}'`).join(',')}]`;


// Find protocols whose name (or first word) appears as a whole word in the headline.
// \y is PostgreSQL's word-boundary assertion — prevents "Meta" matching "MetaDAO",
// "Free" matching "freeze", etc. SQL replace() escapes . and + in names like ether.fi.
// Both branches use ~* (case-insensitive) so "chainlink" matches "Chainlink".
export async function searchProjectInfo(text) {
  if (!text) return { protocols: [] };
  try {
    const { rows } = await pool.query(
      `SELECT slug, name, category, chains, chain_tvls, tvl_usd::float8, tvl_change_1d::float8,
              url, logo_url, token_symbol, gecko_id
       FROM defillama_protocols
       WHERE (
         -- full name word-boundary match. Single-word Titlecase/all-caps names
         -- ("Exodus", "Strike", "Flow") are matched CASE-SENSITIVELY so the common
         -- -English-word usage (lowercase) is skipped; mixed-case / dotted / digit
         -- names (ether.fi, 1inch) keep the case-insensitive match.
         lower(name) != ALL(${STOP_SQL})
         AND (
           (name ~ '^([A-Z][a-z]+|[A-Z]+)$'
             AND $1 ~ ('\\y(' || name || '|' || upper(name) || ')\\y'))
           OR
           (name !~ '^([A-Z][a-z]+|[A-Z]+)$'
             AND $1 ~* ('\\y' || replace(replace(name, '.', '[.]'), '+', '[+]') || '\\y'))
         )
         OR (
           -- first word of multi-word name, length ≥ 4, not a generic word.
           -- The stop-list mirrors entities.GENERIC_TERMS so junk sub-products don't
           -- piggyback on a generic prefix: "Yield Basis" must not tag every "yield"
           -- headline, "Vault Bridge" every "vault", "Credit Coop" every "credit",
           -- "Lending by AlphaFi" every "lending".
           name LIKE '% %'
           AND length(split_part(name, ' ', 1)) >= 4
           AND lower(split_part(name, ' ', 1)) != ALL(${STOP_SQL})
           AND $1 ~* ('\\y' || replace(replace(split_part(name, ' ', 1), '.', '[.]'), '+', '[+]') || '\\y')
         )
       )
       -- Suppress canonical bridges unless the text explicitly mentions "bridge"
       AND NOT (category = 'Canonical Bridge' AND $1 !~* '\\ybridge\\y')
       ORDER BY tvl_usd DESC
       LIMIT 5`,
      [text]
    );
    return { protocols: rows };
  } catch {
    return { protocols: [] };
  }
}


// Find entity_memory entities whose name or aliases match the text as whole words.
// CASE is the key signal: in sentence-case digest prose a brand appears capitalized
// ("Exodus", "Flow", "AERO") while the common-English-word usage is lowercase
// ("Ethereum Foundation exodus", "cash flow", "bold move"). So single-word Titlecase /
// all-caps brand names are matched CASE-SENSITIVELY (Path 3) — this kills the whole
// common-word false-positive class without an endless stop-list. Distinctive
// mixed-case / dotted / digit names (ether.fi, dYdX, USDe, LayerZero) are never English
// words, so they keep the cheaper case-insensitive alias / first-word match.
// Matching paths:
//   1. Alias match          — alias length ≥ 4, not a stop-word; the bare lowercased
//      name of a Titlecase/all-caps brand is excluded here (governed by Path 3).
//   2. First-word of multi-word name — first word ≥ 6 chars (prevents "Aave Labs"→"Aave",
//      "Base Commerce"→"Base" false positives; keeps "Snapshot"→"Snapshot Labs")
//   3. Single-word Titlecase/all-caps name — CASE-SENSITIVE. Titlecase ≥4 ("Flow",
//      "Exodus", "Aerodrome") needs only correct case; 3-char or all-caps tickers
//      ("AERO", "PYUSD") also need mention_count ≥ 6 + a real type (blocks SEC/ICE/CME).
// type='other' is excluded entirely — those are news outlets, tools, regulators.
export async function searchEntityMemory(text) {
  if (!text) return [];
  try {
    const { rows } = await pool.query(
      `SELECT e.slug, e.name, e.type, e.twitter_handle,
              COALESCE(p.logo_url, e.logo_url) AS logo_url,
              p.category, p.tvl_usd::float8, p.gecko_id,
              EXISTS (SELECT 1 FROM entity_avatars av
                      WHERE av.slug = e.slug AND av.image IS NOT NULL) AS avatar_cached
       FROM entity_memory e
       LEFT JOIN defillama_protocols p ON p.slug = e.slug
       WHERE e.mention_count >= 2
         AND e.type NOT IN ('other')
         AND (
           -- Path 1: alias word-boundary match (≥4 chars, not a stop-word).
           -- The bare lowercased name of a single-word Titlecase/all-caps brand is
           -- skipped here so its common-English-word usage can't leak via the
           -- lowercased alias — Path 3 governs those case-sensitively.
           EXISTS (
             SELECT 1 FROM unnest(e.aliases) AS alias
             WHERE length(alias) >= 4
               AND alias NOT LIKE '@%'
               AND lower(alias) != ALL(${STOP_SQL})
               AND NOT (e.name ~ '^([A-Z][a-z]+|[A-Z]+)$' AND lower(alias) = lower(e.name))
               AND $1 ~* ('\\y' || replace(replace(alias, '.', '[.]'), '+', '[+]') || '\\y')
           )
           -- Path 2a: full multi-word name as a phrase — the distinctive whole name
           -- always matches ("Gnosis Safe", "Coinbase Prime") regardless of aliases.
           OR (
             e.name LIKE '% %'
             AND $1 ~* ('\\y' || replace(replace(e.name, '.', '[.]'), '+', '[+]') || '\\y')
           )
           -- Path 2b: first word of multi-word name, length ≥ 6, not a generic word
           -- (Snapshot→"Snapshot Labs", LayerZero→"LayerZero Core", etc.). Suppressed
           -- when that word is itself a standalone parent entity (mention_count ≥ 5):
           -- otherwise a child ("Ethereum Joseph", "Gnosis Safe") would tag every bare
           -- parent mention ("Ethereum", "Gnosis"). The parent entity covers that case;
           -- the child still matches its full name via Path 2a. Also excludes overly
           -- generic first words ("Native Markets"→"native", "Funding Commons"→"funding").
           OR (
             e.name LIKE '% %'
             AND length(split_part(e.name, ' ', 1)) >= 6
             AND lower(split_part(e.name, ' ', 1)) != ALL(${STOP_SQL})
             AND NOT EXISTS (
               SELECT 1 FROM entity_memory o
               WHERE o.slug <> e.slug AND o.name NOT LIKE '% %'
                 AND o.mention_count >= 5
                 AND lower(o.name) = lower(split_part(e.name, ' ', 1))
             )
             AND $1 ~* ('\\y' || replace(replace(split_part(e.name, ' ', 1), '.', '[.]'), '+', '[+]') || '\\y')
           )
           -- Path 3: single-word Titlecase/all-caps brand — CASE-SENSITIVE match against
           -- the name (and its all-caps form) so "Flow"/"FLOW" hit but "flow" does not.
           OR (
             e.name ~ '^([A-Z][a-z]+|[A-Z]+)$'
             AND lower(e.name) != ALL(${STOP_SQL})
             AND (
               length(e.name) >= 6
               OR (e.name ~ '^[A-Z][a-z]+$' AND length(e.name) BETWEEN 4 AND 5)
               OR (length(e.name) BETWEEN 3 AND 5 AND e.mention_count >= 6
                   AND e.type IN ('protocol', 'chain', 'dao', 'exchange', 'fund'))
             )
             AND $1 ~ ('\\y(' || e.name || '|' || upper(e.name) || ')\\y')
           )
         )
       ORDER BY e.mention_count DESC
       LIMIT 6`,
      [text]
    );
    return rows;
  } catch {
    return [];
  }
}


// ── Narratives ──────────────────────────────────────────────────────────────
// Resolve entity slugs → display objects (name, logo, type) for chips.
export async function resolveEntities(slugs) {
  const list = [...new Set((slugs || []).filter(Boolean))];
  if (!list.length) return {};
  const rows = await safeRows(
    `SELECT e.slug, e.name, e.type, e.twitter_handle,
            COALESCE(p.logo_url, e.logo_url) AS logo_url,
            p.slug AS protocol_slug, p.tvl_usd::float8 AS tvl_usd,
            EXISTS (SELECT 1 FROM entity_avatars av
                    WHERE av.slug = e.slug AND av.image IS NOT NULL) AS avatar_cached
     FROM entity_memory e
     LEFT JOIN defillama_protocols p ON p.slug = e.slug
     WHERE e.slug = ANY($1)`,
    [list]
  );
  return Object.fromEntries(rows.map(r => [r.slug, r]));
}
