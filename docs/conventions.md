# Conventions & gotchas

The full, history-carrying version. CLAUDE.md keeps the one-line must-knows; this file holds the why.

## Build / runtime

- **app/ baked into image**: after any Python change → `docker compose build bot && docker compose up -d bot
  monitor`.
- **Async + blocking**: PTB is asyncio; wrap all DB/LLM/Ollama calls in `asyncio.to_thread()` in handlers and
  crons. Never call blocking code from an async context.
- **LLM returns tuples**: `llm.complete()` / `llm.run_agent()` return `(content, model_used)` — always unpack
  both; callers record `model_used` to DB.
- **Telegram HTML only**: prompts emit `<b>`/`<a>` only. Always pass LLM output through
  `telegram_html.sanitize()`. Never send raw model output.
- **`app/db/` is a domain-split package, not one file.** `_core` owns the pool + `_conn` + the
  `_fetchall`/`_fetchone`/`_execute` helpers; each domain module (`feeds`, `digest`, `protocols`, `entities`,
  `analysis`, `threads`, `audio`, `weekly`, `governance`, `podcasts`, `narratives`) owns its tables' queries and
  imports ONLY from `_core` (never from a sibling — keeps it acyclic). `db/__init__.py` re-exports the whole flat
  API (every public fn + `_conn`), so callers keep `from . import db` → `db.foo()`. **A new query goes in the
  module that owns its table** (and, if public, gets added to that module's `from .X import (…)` line in
  `__init__.py`) — don't grow a monolith back.
- **`db` query helpers**: single-statement accessors use `_fetchall(sql, params, dict_rows=)`, `_fetchone(...)`,
  and `_execute(sql, params)→rowcount` from `_core` instead of re-opening the
  `with _conn() … cursor … execute … fetch` boilerplate. Reach for the raw `with _conn() as conn, …` block ONLY
  for things a helper can't express in one statement: multi-statement transactions (e.g. `set_cache`,
  `replace_narratives`), `executemany`/`execute_values`, `SET LOCAL` + query (`search_feed`), or per-query
  rollback fallbacks (`insert_feed_items`). Don't paste a fresh cursor block for a plain read/write.
- **Caddy hot-apply**: `Caddyfile` bind-mounted by inode — editing creates a new inode. Always
  `--force-recreate caddy`. The static landing dir (`deploy/landing/` → `/srv/landing:ro`) is also
  bind-mounted but served file-by-file, so editing its HTML/CSS/assets is live on the next request — **no
  recreate needed** for landing-only edits.
- **Domain split (2026-06-20)**: apex `horyon.xyz` = static marketing landing (`deploy/landing/`, Caddy
  `file_server`); the Next product = `app.horyon.xyz`. Apex keeps `/tg/*` (Telegram webhook base unchanged)
  + `/monitor*`, and 301s old app paths (`/d/*`, `/narratives`, `/map`, `/weekly`, `/threads`, `/api/*`) to
  the app subdomain. **`app.horyon.xyz` needs a DNS A record before deploy** (Caddy provisions its TLS cert
  on first request). App canonical/OG/sitemap URLs all use `https://app.horyon.xyz`; the landing has its own
  static `robots.txt`/`sitemap.xml`/canonical at the apex.
- **Startup jobs are staggered** (`main.py` `next_run_time` offsets): ingest fires immediately, then protocols
  +60 s, snapshot +90 s, podcasts +180 s, narratives +300 s, entity_graph +480 s — so the LLM-heavy boot jobs
  don't burst the free-tier rate limit on a restart. Add new LLM-heavy boot jobs with their own offset, not
  `now()`.

## pgvector / embeddings

- **pgvector recall**: `db.search_feed()` must `SET LOCAL ivfflat.probes` per query — without it the 30-day
  window returns ~1 result.
- **`SET LOCAL` syntax**: does not accept `$N` params in node-postgres. Interpolate safe integer constants
  directly: `` `SET LOCAL ivfflat.probes = ${PROBES}` ``. psycopg2 `%s` with integers is fine (mogrified
  client-side).
- **Ollama embed context window**: `nomic-embed-text` has a 2048-token context; a long article/transcript 500s
  with "input length exceeds the context length" and leaves the row un-embedded forever (retried every cycle).
  `embeddings.embed()` truncates to `EMBED_MAX_CHARS=6000` (word-boundary) and halves-and-retries on the specific
  error. Keep the cap. (The public web search route no longer embeds — its free-text path is Postgres FTS, see
  `web/api/search`.)
- **Public web free-text search = Postgres FTS, not pgvector**: `feed_items.content_tsv` is a generated
  `tsvector` (HTML stripped via `regexp_replace` — IMMUTABLE, so allowed in a generated column) with a GIN index;
  query via `websearch_to_tsquery('english', $1)` (never throws on junk, empty tsquery → 0 rows → word-boundary
  fallback). This is the scalable, zero-external-call replacement for the old per-query Ollama embed on the public
  site. pgvector recall is still the bot/ingest path, not the web path.

## Entity matching, dedup & hygiene

- **Word-boundary matching**: Python `\b`, PostgreSQL `\y`. Never use `ILIKE '%word%'` — false positives
  ("free"→"freeze", "meta"→"MetaDAO").
- **entity_memory schema**: `type` ∈ `protocol|chain|fund|person|exchange|dao|other`. `twitter_handle` separate
  from `aliases`. `upsert_entity_from_coingecko()` never overwrites type/mention_count/twitter_handle. ERC-XXX /
  EIP-XXX blocked at upsert and in extraction prompt.
- **Entity quality gates**: `extract_and_upsert_entities` caps at **15 entities/ingest run** (newest-first) and
  rejects aliases that are numeric, <3 chars, ERC/EIP-XXX, or in `entities.GENERIC_TERMS` (both alias filters use
  `GENERIC_TERMS`, not the smaller `BLOCKED_ALIASES`).
- **Shared runtime matcher gate (`entities.matchable_term`)**: EVERY runtime word-boundary matcher (narratives
  `_EntityMatcher` + `entity_graph`) routes terms through it. Rejects @handles, digits, `entities.GENERIC_TERMS`
  (superset of `BLOCKED_ALIASES` + crypto vocab + common English words that are also entity names: across/
  strategy/public/movement/bullish/push/…), and short ambiguous tokens (<6 chars need ≥4 len; 3–5-char tickers
  need a distinctive type + ≥10 mentions). **This is the ONE place to add a runtime false-positive fix** (e.g.
  "Across" was matching the word "across" 254×). Multi-word names unaffected. Do **not** add real distinctive
  protocol names (Morpho/Fluid).
- **Feed sources must be crypto-pure — ingest has NO relevance gate**: `clean_items` filters only on
  length/RT/dedupe; every URL in `feeds.SOURCES` is trusted to be crypto-only. A source can silently rot into a
  general-news/AI-slop aggregator that frames every story as "…crypto impact" (cryptobriefing did this — World Cup,
  geopolitics, SpaceX — and was 26.8% of the corpus before removal 2026-06-19). A keyword filter can't catch that
  framing → **drop the source, don't filter**, then `DELETE FROM feed_items WHERE link ILIKE '%domain%'` and rebuild
  narratives + entity_graph (both re-scan `feed_items`). Periodically eyeball per-source content.
- **Non-RSS sources scrape into the SAME `feed_items` shape, not a side table**: a publisher with no RSS/API
  (e.g. Kaiko — `app/kaiko.py`) gets its own module + cron, but it must produce raw item dicts (`link`, `title`,
  `content`, `creator`, `pub_date`, `categories`) and route them through `ingest.clean_items` →
  `sanitize_items` → `db.insert_feed_items`, so the digest/narratives/entity-graph/search pick them up with
  zero downstream changes. Use stdlib `urllib` + `re`/`json` (no new dep — mirror `podcasts.py`); pre-filter
  candidate URLs with `db.existing_links()` so the cron re-fetches only genuinely new pages; cap fetches per
  run. Because ingest has no relevance gate (above), do the editorial-vs-marketing filter IN the scraper
  (Kaiko: a breadcrumb-category denylist) — don't dump a publisher's product/case-study pages into the feed.
- **Web "Recent mentions" gates ambiguous names (`api/search` `entityFeedRows`)**: entity-tag clicks match raw feed
  text by the longest token (`\yTOKEN\y`). Single common-English-word names (`AMBIGUOUS_TERMS`: base/story/strategy/
  across/…) additionally require a `CRYPTO_CTX` signal in the same item, so clicking "Base" can't surface a "fan
  base" headline. This is the web-search-route twin of `matchable_term`/`TAG_STOPWORDS` (separate because this route
  matches raw text, not entity rows) — add a new common-word entity name to `AMBIGUOUS_TERMS` there.
- **Entity dedup (`entity_audit merge`)**: groups by `(type, suffix-stripped name)` — folds version/suffix
  variants + a curated `_EXPLICIT_MERGES` list. **Same-type guard** stops heuristic cross-type folds (Coinbase
  exchange ≠ Coinbase Ventures fund), **but `_EXPLICIT_MERGES` bypasses it** — use it for genuine cross-TYPE /
  cross-FORM same-entity dups (`ether`→`ethereum`, `kelp`↔`kelp-dao`, `zcash`↔`zec`, `bsc`↔`bnb-chain`,
  `hype`→`hyperliquid`). Each set must be the SAME project — never parent/child of different scope (do NOT fold
  `ondo-global-markets`/`ondo-yield-assets` into `ondo-finance`: distinct DeFiLlama protocols → use `dealias`).
  Canonical survivor prefers a slug in `defillama_protocols` (keeps TVL+logo join), then highest mention_count.
  Run `--dry-run` first; after a merge, rebuild narratives + entity_graph (slugs change).
- **Piggyback-alias stripping (`entity_audit dealias`)**: the durable fix for "wrong entity tagged because it
  shares a big entity's ticker". A distinctive token belonging to ONE dominant entity gets attached as an alias to
  small look-alikes (`hype` ended up on Hyperion DeFi / Hyperbeat / Hyperliquid HLP; `ondo` on Ondo Global Markets
  / Yield Assets). `dealias` finds, per token, the owner = max-mc holder; if the owner is ≥10mc AND ≥3× the next
  holder, it strips the token from every OTHER holder that is both < owner.mc/3 AND ≤10mc. Never strips an entity's
  own slug/normalised-name, never orphans, skips hyphenated/`$`/`@` tokens. Prevention: `ENTITY_EXTRACTION_SYSTEM`
  forbids giving a sub-product the parent's ticker/bare name as an alias. Part of `entity_audit all`.
- **Web entity-tag matcher ≠ the runtime gate**: web bullet tags come from `searchEntityMemory`/`searchProjectInfo`
  (own SQL stop-lists), **not** `matchable_term`. Two false-positive classes: (1) **path-2 first-word
  piggybackers** (`Ethereum Foundation` matched every "Ethereum", `Morpho Blue` every "Morpho") — collapsed in
  `BulletItem.buildEntities`/`InlineTags`: entity_memory tags deduped among themselves by first word (≥4 chars,
  shortest name wins), seeded by the DeFiLlama first-words. (2) **piggyback aliases** — now automated by
  `entity_audit dealias`.
- **DeFiLlama tag matcher was the bigger offender**: `searchProjectInfo` path-2 matched junk sub-products on a
  generic prefix (`Yield Basis` on every "yield", `Vault Bridge` on "vault"). Fix: both stop-lists mirror
  `GENERIC_TERMS` (incl. swap/swaps/pools/dex), and `BulletItem` merges chains + DeFiLlama protocols +
  entity_memory into ONE list deduped by first word with shortest-display-wins (`buildEntities` + `pickBetter`). To
  verify rendered tags, reproduce both SQLs + the unified dedup per bullet.
- **Stop-lists CONSOLIDATED into one shared `TAG_STOPWORDS`**: `web/lib/db.js` had FOUR divergent inline stop-lists
  that drifted (`Current` tagged "current value", `Team Finance` any "team"). Now ONE module-level `TAG_STOPWORDS`
  → `STOP_SQL` interpolated into all four spots. **Add a new false-positive word HERE once.**
  `searchEntityMemory` path-3 (short 3–5-char distinctive names) threshold lowered `≥10 → ≥6` (+ now gated by
  `TAG_STOPWORDS`) so well-known short tickers tag (HTX, dYdX, UMA, Aptos, Dai, Linea, MEXC, Upbit).
  `buildEntities` first-word key is punctuation-stripped (`ether.fi`→`etherfi`) so `EtherFi Cash Liquid` folds
  onto `ether.fi`.
- **Alias-smear across unrelated entities**: a ticker shared by look-alikes via a wrong alias produces cross-
  tagged false positives the first-word dedup can't fold. `strc` (Strategy's preferred stock) was wrongly aliased
  on `storj`/`storichain`/`starknet`/`starcat`. Fix: strip the ticker off every non-owner, keep it on the
  dominant owner only; set the real owner's `type` so it tags. `dealias` catches ≥3×-dominance cases; hand-strip
  the rest. `0x` (2-char, digit-leading) and `Orchard` (Zcash shielded pool) remain hard cases — left as-is.
- **CASE is the signal for common-word brands (single biggest FP class)**: many entities are single words that are
  ALSO common English words (`Exodus`, `Flow`, `Render`, `Strike`, `Spark`, `Across`, `Dune`, `Forward`). In
  sentence-case digest prose the BRAND is capitalized (`Exodus`, `Flow`, `AERO`) while the common-noun usage is
  lowercase (`Ethereum Foundation exodus`, `cash flow`, `bold move`). So single-word **Titlecase/all-caps**
  (`^([A-Z][a-z]+|[A-Z]+)$`) names are matched **CASE-SENSITIVELY** against the name + its all-caps form —
  `web/lib/db.js` `searchEntityMemory` path-3 + `searchProjectInfo`, mirrored in `app/entities.py`
  (`_load_matchers`/`_cs_pattern`: the slug AND bare name are kept OUT of the case-insensitive alias map for these,
  matched only by the case-sensitive pattern). This kills the whole class WITHOUT a stop-list, so **do NOT add a
  brand-that-is-also-a-word to `TAG_STOPWORDS`/`GENERIC_TERMS`** — that would block it even when correctly
  capitalized; let case handle it. Mixed-case / dotted / digit brands (`ether.fi`, `dYdX`, `USDe`, `LayerZero`) are
  never English words → keep the cheaper case-insensitive alias/first-word match. `TAG_STOPWORDS`/`GENERIC_TERMS`
  are now only for (a) crypto-generic vocab and (b) junk single-word **aliases** (`hard`→Kava Lend, `link`→Chainlink,
  `move`→Movement) — common words that are not a brand name.
- **Path-2 parent/child piggyback (web)**: a multi-word child (`Ethereum Joseph`, `Gnosis Safe`) matched every bare
  parent mention (`Ethereum`, `Gnosis`) via the first-word path. `searchEntityMemory` now splits path-2 into **2a**
  full-name phrase match (always) + **2b** first-word match suppressed when a standalone parent entity
  (`mention_count ≥ 5`) owns that word — the parent covers the bare mention; the child still matches its full name
  via 2a. (`app/entities.py` matches full names/aliases only, never bare first words, so it's unaffected.)
- **Re-run `entity_audit merge` periodically**: duplicate entity rows (`aero`/`aerodrome`/`aerodrome-finance`,
  ticker==project, type-split same project) reappear as extraction adds rows; each dup is a redundant chip. `merge`
  (curated `_EXPLICIT_MERGES` + suffix-stripped `_merge_key` heuristic) folds them into the DeFiLlama-canonical slug.
  Web tags read `entity_memory` live, so a merge takes effect immediately (no narratives/graph rebuild needed for
  tags, but rebuild those after slug changes per the schema rule).
- **Stale-entity decay**: `db.decay_stale_entities()` runs post-digest — `mention_count ×0.8` for entities idle
  >14d (floored at 1), prunes `type='other'` with ≤2 mentions idle >30d. The `updated_at` guard caps decay at
  once/week.

## Anti-hallucination & auditing

- **Temporal-modality hallucination (`known_facts.py`)**: the pipeline kept upgrading tense — *announced /
  "coming to" / testnet* rewritten as *live / deployed*. Motivating case: Circle's **Arc** (unreleased testnet)
  reported as "Uniswap deploys on the Arc chain". This is NOT a source-trust problem — the model collapsing
  nuance. Fix: (1) a **PRESERVE TEMPORAL MODALITY** rule in `_DIGEST_RULES` + `BULLET_ANALYST_SYSTEM` +
  `THREAD_SYSTEM` + `WEEKLY_SYSTEM` + `ENTITY_BRIEF_SYSTEM` + `ANALYST_EXTRACTION_SYSTEM`; (2) `known_facts.py`
  injected as the top context block in **all six** LLM write-paths, overriding source spin AND the LLM-written
  `entity_memory.summary`; (3) entity hygiene (set `arc` type=`chain`, dated testnet summary, fold the dup).
  **When a recurring factual error isn't in the source, add a `known_facts` entry — don't just patch one prompt.**
  - **The inverse error — a LIVE entity mislabeled pre-launch (`known_facts.ESTABLISHED_MAINNET`)**: the pre-launch
    auto-discovery in `audit.prelaunch_entities` flags any entity whose `entity_memory.summary` contains the bare
    word *testnet* (etc.), which mis-fires when an ESTABLISHED mainnet chain/protocol ships an upgrade/bridge/feature
    to *some* testnet (Base: "Sepolia testnet supports Beryl upgrade…" → whole chain wrongly gated + warned as
    unreleased; Pendle: "contract live on Monad testnet"). Fix: list the slug in `known_facts.ESTABLISHED_MAINNET` —
    auto-discovery skips it, so it's never auto-flagged pre-launch. `known_facts` is now decoupled from "pre-launch":
    it is curated ground truth that may hold either a pre-launch fact (Arc) OR a positive "this is live" correction
    (the `base` fact), and `ESTABLISHED_MAINNET` is what decides which entries gate modality. An entity must not be
    treated as both. Use a POSITIVE known_fact (+ `ESTABLISHED_MAINNET`) when the model keeps collapsing a live
    entity's *sub-feature* testnet status onto the entity itself.
- **Auditing stored data (`app.audit`)**: `scripts/llm_quality_audit.py` checks *live* LLM paths; `python3 -m
  app.audit` checks what's already WRITTEN to the DB — modality, overstatement, thread URL grounding, entity-alias
  hygiene. `--fix` applies only safe self-healing structural fixes (delete known_facts-entity briefs, blank
  modality-flagged summaries, remove junk aliases + strip bare generic-word aliases) and prints the regen command
  for flagged prose (never rewrites it). The modality check auto-scopes to known_facts entities AND any entity
  whose own summary declares it pre-launch/testnet. Run after seeding a `known_facts` entry (clears stale cache)
  and as a pre-deploy gate (exits non-zero on hard issues). **Two entity classes stay review-only (judgment, never
  auto-fixed):** (1) **generically-NAMED junk entities** — the name is itself a generic word with no distinctive
  alias (`Gas`/`Bridge`/`Oracle`, mc≤2); DELETE obvious noise but KEEP a real project named a generic word
  (`Bullish` exchange, `Idle` Finance). (2) the **cross-type alias-collision** list — `entity_audit merge` does
  NOT fold these; a same-NAME cross-type dup (one side mc=1, 0 edges) is a manual DELETE of the junk side, while
  token/chain/protocol facets (`avax`, `monero`/`xmr`) are left alone. After any slug/alias/delete change rebuild
  narratives + entity_graph (skip if touched rows were all mc=1 / 0 edges). Driven by the `/audit-data` skill.

## Digest / backfill / data sync

- **Entity intel briefs**: `get_entity_intel_brief(query)` checks canonical name then aliases via `entity_memory`
  (7-day freshness). No direct `entity_brief` → `digest` import (circular) — uses
  `db.get_digest_contents_for_dedup`.
- **chain_tvls filtering**: `defillama_protocols.chain_tvls` includes internal keys like `"Ethereum-borrowed"` —
  the frontend filters keys containing `"-"` or `"borrowed"`. Keep this filter.
- **Bullet analyses sync**: `--regen-analyses` + backfill call `db.delete_bullet_analyses(date)` first (full
  reset). `run_digest` instead **prunes** post-generation via `db.prune_bullet_analyses(date, current_titles)` —
  so re-running the digest same-day (which merges on the cached prior run) doesn't leave stale-title rows the
  narrative layer would double-count. Prune runs only after a successful generation and keeps ALL current titles.
  `digest_bullet_analysis` feeds the web + narratives + ingest auto-regen only — **not** the digest prompt (that
  uses `crypto_cache` A↔B merge + `crypto_digest` chain/dedup + `analyst_notes`).
- **Dedup context**: `get_digest_contents_for_dedup(days, before_date=None)` — `before_date` used by backfill to
  avoid reading future digests.
- **Weekly dedup**: `WEEKLY_SYSTEM` instructs the LLM not to repeat Key Stories from prior weeks unless a concrete
  new outcome exists.
- **Backfill keys feed items by `pub_date`**: `get_feed_items_for_date` uses `COALESCE(pub_date, ingested_at)` —
  startup bulk-seed data can have `ingested_at` lag `pub_date` by a day+, which made the earliest backfill date
  find zero items + skip.
- **Long backfills: run DETACHED** — `docker exec -d horyon-bot sh -c "LLM_MAX_CALLS_PER_MIN=20 python3 -m
  app.backfill_digests > /tmp/bf.log 2>&1"`. An attached `docker exec` (even `run_in_background`) dies when the
  client connection drops, killing the run mid-way; `-d` reparents it to container init. `_build_and_store_digest`
  calls the LLM + empty-guard BEFORE deleting the old row, so a failed/empty date keeps its existing digest; the
  per-date loop catches exceptions. Resume with `--from-date`. Backfill is idempotent for digests/analyses
  (delete+insert) but `analyst_notes` is insert-only → clear the date range first.

## Narratives

- **Narratives are a full rebuild, not incremental**: `narratives.build_and_store()` wipes both narrative tables
  and re-inserts (`db.replace_narratives` → `DELETE FROM narratives` cascades `narrative_signals`). Slugs are
  re-derived each run but stay stable via entity-overlap reuse (which carries the prior thesis forward to skip the
  LLM). Empty until the first `python3 -m app.narratives` (or the next post-digest/3 h cron). Momentum is
  time-anchored, so re-running with no new signals still re-evaluates states as time passes. `replace_narratives`
  takes a `pg_advisory_xact_lock` so the three triggers (post-digest ~07:00, 3 h cron, manual CLI) can't run two
  DELETE+INSERT cycles concurrently. Keep the lock if you add another rebuild path.

## LLM provider chain

- **LLM provider chain (NIM → OpenRouter), set in `.env`** — bot reads `.env` via `env_file`; web gets the same
  vars through explicit `environment:` entries in docker-compose (`NIM_API_KEY`, `NIM_BASE_URL`, `NIM_MODELS`,
  `LLM_TIMEOUT_SEC`, `OPENROUTER_*`). The web mirrors the bot via `web/lib/llm.js`.
- **Always keep MULTIPLE models across the chain.** A single model = no fallback: when a free provider returns
  `503 "no healthy upstream"` the whole digest fails (this killed the 2026-06-03 07:00 run, pinned to one
  model). The chain falls through on any error, retrying only brief 5xx/conn blips once. **Probe a model live
  before adding it** — a 404 slug wastes a hop, and reasoning models can be very slow on NIM free tier. Current
  chain: NIM `mistralai/mistral-medium-3.5-128b` (primary) → NIM `deepseek-ai/deepseek-v4-flash` (slow fallback) →
  OpenRouter `gpt-oss-120b:free` → `nemotron-3-super-120b-a12b:free` → `llama-3.3-70b:free`. NIM free tier
  cold-starts ~20–40s; `LLM_TIMEOUT_SEC=60` lets mistral finish yet fails over before stalling. Do NOT enable NIM
  "thinking"/`reasoning_effort` globally — it eats the budget on small-`max_tokens` calls (scoring uses 120).
- **Rate limiter for bulk regen**: `config.LLM_MAX_CALLS_PER_MIN` (default 0 = off) caps provider calls across a
  sliding 60s window, shared across threads (`llm._rate_limit`). Set it per-run for backfills — `docker exec -e
  LLM_MAX_CALLS_PER_MIN=20 …`. Process-local, so **don't run two backfill processes at once** (limiters don't
  coordinate → combined rate doubles).

## Web specifics

- **Web fonts load via `<link>` in `app/layout.js`, NOT an `@import` in `globals.css`.** A CSS `@import` is
  discovered only after `globals.css` has downloaded + parsed, serializing two round-trips on the critical render
  path. The `<head>` carries `<link rel="preconnect">` to `fonts.googleapis.com` + `fonts.gstatic.com` (the latter
  `crossOrigin`) then the font `<link rel="stylesheet" … display=swap>`, so the font CSS downloads in parallel with
  ours and the TLS handshake is pre-warmed. `web/middleware.js` CSP must keep `style-src … fonts.googleapis.com` +
  `font-src … fonts.gstatic.com`. **Don't re-add the `@import`.** (The browser fetches the fonts; this does not
  violate the container's zero-egress rule.)
- **Shared display formatters live in `lib/format.js`; shared SVG icons in `app/components/icons.js`.** Import
  `fmtTvl`/`fmtAgo`/`XIcon`/`ExtIcon` rather than re-pasting a local copy. Only fork a local variant when the
  output *intentionally* differs (documented in `lib/format.js`).
- **`lib/db/` is a domain-split package, not one file.** `_core.js` owns the shared `pool` + the
  `safeRows`/`safeOne`/`iso` helpers; each domain module (`digest`, `threads`, `audio`, `tvl`, `governance`,
  `podcasts`, `weekly`, `analysis`, `entities`, `narratives`) owns its tables' queries and imports ONLY from
  `./_core.js` (the one cross-domain edge is `narratives` importing `resolveEntities` from `./entities.js`).
  `db/index.js` re-exports the flat API (`export *` per module + `export { pool }`), so callers keep
  `import { … } from "../lib/db"` unchanged. A new query goes in the module that owns its table; if it's public,
  it's picked up automatically by that module's `export *` — **don't recreate a monolithic `db.js`** (and never
  leave both `lib/db.js` and `lib/db/` present — the bundler resolves `lib/db.js` first and the package is ignored).
- **`lib/db` query helpers**: single-query read accessors use `safeRows(text, params, fallback=[])` /
  `safeOne(text, params, fallback=null)` (run the query, degrade to the fallback on a transient DB error so a
  public surface shows an empty section, never a 500) and `iso(v)` to serialize a `timestamptz` to ISO/null —
  not a hand-rolled `try { const { rows } = await pool.query(…) } catch` + `v ? v.toISOString() : null`. All three
  live in (and are exported from) `_core.js`. Functions that fire MULTIPLE queries in one body (narratives, both
  graphs, `getEntityIntelBrief`) keep their own `try/catch` so a mid-sequence failure degrades the whole result;
  no-catch critical-path readers (`getDigest`, `latestDate`, …) intentionally let errors propagate.
- **Mobile sidebar stacking** (legacy): `.shell { position: fixed }` creates a CSS stacking context — a mobile
  backdrop must be portalled into `.shell` via `createPortal`, not `document.body`.
- **`next/og` font format**: `@vercel/og` (bundled in Next.js 14.x) accepts **TTF/OTF only** — woff/woff2 throw
  "Unsupported OpenType signature". Load fonts via Google Fonts v1 API **without a User-Agent** header:
  `fetch('https://fonts.googleapis.com/css?family=Raleway:800')`. Node's default fetch UA causes Google Fonts to
  return a TTF `src` URL; a browser UA returns woff2 → crash. **Never set a UA for font loading.**

## Schema

- `deploy/schema.sql` is authoritative for fresh volumes. All past migrations applied to live DB; individual
  migration files removed. New additive tables (`podcast_episodes`, `narratives`/`narrative_signals`,
  `digest_threads`, `digest_audio`, `entity_edges`) are `CREATE TABLE IF NOT EXISTS` — apply once to the live DB
  on deploy: `docker exec -i horyon-db psql -U crypto -d crypto < deploy/schema.sql` (idempotent).
- **`digest_audio` is keyed `(digest_date, variant)`** (`short`/`standard`/`explainer` — three length variants
  from one digest). Re-keying an existing table is NOT just `CREATE TABLE IF NOT EXISTS`: the schema file also
  carries an idempotent migration (`ADD COLUMN IF NOT EXISTS variant` + a guarded `DO $$` block that repoints the
  PK from `digest_date` to `(digest_date, variant)` only if it isn't already composite). Re-run the schema apply
  above on deploy so existing single-PK volumes migrate (old rows default to `variant='standard'`). The bot
  renders all three per digest (`briefing.build_all_variants_for_date`); Telegram + any legacy `db.get_audio_*`
  caller default to `variant='standard'`. Table-level `GRANT SELECT` already covers the new column → no
  `web_db_role.sql` re-run needed.
- **Audio variant durations must stay ordered `short < standard < explainer` — a single LLM pass won't do it
  alone.** Models undershoot a long word target, so the ~12-min deep dive once rendered SHORTER than the ~6-min
  standard. Length is enforced post-generation, not just asked for: a per-variant word FLOOR
  (`target_words × BRIEFING_MIN_WORD_RATIO`) re-prompts an EXPAND pass (`prompts.build_briefing_expand_user`) up
  to `BRIEFING_EXPAND_ROUNDS` times keeping the longest valid draft, and the explainer floor is additionally
  raised to `standard_words × BRIEFING_EXPLAINER_OVER_STANDARD` so the deep dive can't land at/below the briefing.
  Don't "fix" a short deep dive by only bumping `target_words` (the prompt) — the floor + expand loop in
  `briefing._build_script` is the actual lever; also keep `BRIEFING_EXPLAINER_MAX_BULLETS > BRIEFING_MAX_BULLETS`
  (story count is the other duration lever — the deep dive needs more raw material to go deep on).
- **Snapshot spaces cache**: module-level list, 24 h TTL, per-process (cold-fetch on restart). Do not persist to DB.

## Podcast transcripts (YouTube on this Azure host)

YouTube blocks unauthenticated caption scraping from datacenter IPs — `youtube-transcript-api` returns
`RequestBlocked`, bare `yt-dlp` returns "Sign in to confirm you're not a bot". **Working setup:** a burner-account
Netscape `cookies.txt` git-ignored at repo root, mounted read-only into `bot` at `/secrets/cookies.txt`
(docker-compose `volumes`), with `PODCAST_YTDLP_COOKIES=/secrets/cookies.txt`. `fetch_transcript` prefers the
`yt-dlp` path. Two non-obvious behaviours **must stay**:
1. `--ignore-no-formats-error` — the image has no JS runtime, so yt-dlp can't resolve *video* formats ("Only
   images available") and would abort *before* writing subs. Captions don't need the n-challenge, so this flag
   lets subtitle-only extraction proceed.
2. yt-dlp rewrites the cookie jar on exit → `_via_ytdlp` copies the mounted (read-only) cookies into the run's
   temp dir first, so the source file is never clobbered.

Cookies expire in ~weeks → re-export from the burner account (the mount picks it up, no rebuild). Alternatively
set `PODCAST_PROXY` (residential proxy). A hard miss marks `status='failed'`, retried only after 24 h (backoff in
`db.get_pending_podcast_episodes`). Shorts/livestreams self-skip. Never breaks the digest.

## Token / cost controls (Claude Code dev env)

Config in `.claude/settings.json` (committed) + root `.gitignore` keep Claude's context lean — none of it touches
the context window, thinking, or model, so no quality loss; it only stops reading files Claude never needs.
- **`.gitignore`** — `.venv/`, `__pycache__/`, `*.pyc`, `node_modules/`, `web/.next/`, `*.log`. ripgrep respects
  it. `.venv` was untracked from git (`git rm -r --cached`); keep it ignored.
- **`permissions.deny`** — hard-blocks `Read` of `.pyc`, `__pycache__`, `.venv`, `node_modules`, `.next`,
  `*.min.{js,css}`, `*.map`, lockfiles.
- **`env.DISABLE_NON_ESSENTIAL_MODEL_CALLS=1`** — disables background Haiku calls (titles, suggestions).
- **Image-read hook** — `PreToolUse(Read)` runs `.claude/hooks/guard-image-read.sh`: auto-denies reads of raster
  images >768px with a reason pointing to `file` for metadata or a downscaled `/tmp` thumbnail. Images ≤768px and
  any `/tmp/*` path pass through silently. Fails open. After editing it, reload via `/hooks`.
- **Delegation to a cheap model** — `scripts/delegate.sh "<self-contained spec>"` sends an isolated implementation
  task to `poolside/laguna-m.1:free` via OpenRouter (override with `MODEL=…`) and prints only the reply. Use it
  **only** for tasks solvable from a minimal self-contained spec (SQL, util fns, small components, tests, isolated
  endpoints). Stay on Claude for architecture, cross-file debugging, or product reasoning. The real saving is
  *context avoidance*, not codegen offload.
- **Deterministic ops are scripts, not skills** — `scripts/status.sh` (stack health), `scripts/logs.sh [service]
  [pattern]`, `scripts/trigger.sh <job>`, `scripts/deploy.sh <bot|web>` (build → restart → wait-healthy → tail),
  `scripts/test_feeds.sh [--new|--audit|<url|handle>…]` (curl-based feed health probe). **Run them directly** — a
  human reads the pre-formatted output with zero Claude tokens. `/deploy-bot` and `/deploy-web` survive only as
  thin stubs that call `scripts/deploy.sh` and let Claude react to a `BUILD FAILED`/`UNHEALTHY` result.

### Creating a new command/skill vs. a script

Before writing a new `.claude/commands/*.md` skill, ask: **is Claude's judgment applied to the result, or is this
just a fixed sequence of commands?**
- **Pure "run and read"** (a fixed command list, an arg→command dispatcher, a formatted report) → write a
  **script in `scripts/`** and document it. A skill would only re-pay its markdown body + tool round-trips on
  every invocation.
- **Judgment on the output** (interpret a failure and fix code, categorize/decide, edit files, multi-step
  reasoning) → a **skill** earns its keep. Push any mechanical part into a script the skill calls (as
  `/deploy-bot` does), so only the reasoning runs in Claude's context.

Rule of thumb: *keep a skill only when Claude reacts to what the script prints.*

## Verifying changes

- `/monitor` — digest history, source health (24h/7d/total per source sorted by 24h DESC), TVL snapshot,
  entity/notes counts.
- `docker compose logs -f bot` — after deploy: watch for `entity extraction: N entities upserted`; errors show as
  `ERROR` / `Traceback`.
- For Telegram: inject a synthetic `Update` via `process_update` — don't test against the live webhook.
- **LLM quality / grounding audit** — `scripts/llm_quality_audit.py` (NOT baked into the image; `docker cp` it in
  + run with `PYTHONPATH=/app`). Captures the EXACT prompt + output of each LLM path and checks programmatically
  for hallucinated URLs/numbers and missing context blocks. Tests: `digest bullet agent analyst narratives`.
  Read-only but makes real LLM calls — run a single test at a time. URL grounding compares by tweet status-ID.
