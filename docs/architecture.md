# Architecture

Deep reference for Horyon's web IA, intelligence pipeline, and repo layout.
CLAUDE.md is the lean index; this file holds the detail. Keep both in sync after non-trivial changes.

## Output modes

- **Daily digest** (`/digest`, auto 09:00 UTC+2) — curated 5–10 bullet summary of last 24 h.
- **Specialized agent** (`/updates <kw>` or free-text) — ReAct agent; checks `entity_intel_brief` cache
  first for instant response, then falls back to the full loop.
- **Web search** (search bar) — same ReAct agent + identical system prompt in a Next.js API route; returns
  Telegram bullet HTML rendered in SearchPanel.

## Web UI — sidebar-free top-nav IA (redesigned 2026-06-03)

The old left sidebar was **removed**. The IA is a header **view switcher** (`MainNav`: Daily · Narratives ·
Entity Map · Weekly) over sibling routes, each = a master list in `.feed-left` + the shared `RightPanel` detail in
`.feed-right`. `.reader` is the fixed full-width shell (the old `.shell` sidebar grid is gone). The Thread composer
(`/threads`) is a still-live route but **no longer surfaced in the nav** (2026-06-19) — bookmark/direct-link only.

- **Daily** (`/d/[date]`, `/`→latest) — `BulletFeed`: a **date-stepper masthead** (`DateNav`: ‹ prev/next
  *digest day* › + a click-to-open calendar popover; `[`/`]` keys step days), **source filters** (`SourceFilter`:
  All · News · Tweets · Podcasts, all-on default, counts, empties disabled), a sort toggle (Importance/Recent),
  the bullet list with **podcasts rendered inline as daily-news rows** (`PodcastFeedItem`, published that UTC
  day, opens `PodcastPanel`), and the market/TVL bandeau.
- **Narratives** (`/narratives`) — `NarrativeView`: a board of narrative cards (state glyph · momentum · thesis ·
  entities · evidence tally) → `NarrativePanel` on select.
- **Entity Map** (`/map`, own top-level nav item as of 2026-06-19 — was previously the `/narratives/map`
  Board·Map sub-toggle) — `EntityGraph`: a co-occurrence map of ALL entities with **three views via a `view`
    toggle (default `board`)** (deps `d3-force`/`-selection`/`-zoom`/`-drag`):
    - **Board** (default) — a **deterministic, carded "tidy clusters" layout**: one titled region per entity
      type, sized by a lightweight count-proportional treemap (rows floored to `MIN_RH` so the smallest type —
      e.g. *Other* — keeps room for its coins), nodes packed in a neat grid inside each region, no force sim.
      On build it **frames the whole board to fit (fitView)** so the bottom-most region is never clipped.
    - **Network** — the original **D3 force-directed graph**: relationships drive position, nodes are
      drag-able, physics tuned to spread (charge ∝ node size, weak centering) + **auto zoom-to-fit** on
      settle / metric change / Recenter; only the top ~16 hubs keep a resting label.
    - **Index** (2026-06-26) — a **ranked, sortable table of ALL tracked entities** (`EntityLeague`, `pl-*`
      namespace, NOT D3 — a clean data table that drops the starfield canvas), not just protocols. The pitch:
      **Horyon's own intelligence signal (coverage + co-mention centrality) across every entity, fused with
      DeFiLlama TVL where it exists.** The spine is `entity_memory`, so coverage (`mention_count`, the default
      sort + a proportional bar) and centrality (`degree`) are universal and exact for every type
      (protocol/chain/exchange/fund/person/other). Columns: rank · entity (avatar/ticker/sector·chains) · **Type**
      (colored dot) · **TVL** (log-scaled magnitude fill) · **7d** net flows (colored ±, proportional fill) ·
      **Coverage** (bar + mention_count + emoji-free narrative trajectory tag `trajectoryMeta`) · **Connected**
      (overlapping avatar stack of the top co-mentioned entities + total degree, sorts by degree). **TVL by
      type:** protocols carry brand-aggregated DeFiLlama TVL (below); **chains** carry their per-chain
      `defillama_tvl` snapshot — a `chain_tvl` CTE joins `lower(defillama_tvl.chain) = entity.slug` for TVL + a
      7d change vs the ~7-day-prior daily snapshot (only the ~6 tracked chains have it). **Connected avatars:** a
      per-row LATERAL pulls the top-5 neighbours from `entity_edges` (either direction), each with its resolved
      avatar fields (logo → twitter → `entity_avatars` cached flag) via `json_agg`.
      Click a header to sort, the SHARED type legend (`TypeChips`, now rendered in all three views) + toolbar
      search filter the set, a row-click reuses `EntityMapPanel` (rows are node-shaped). Data =
      `db.getEntityLeague({limit:200})` (`web/lib/db/tvl.js`), `unstable_cache(revalidate:3600)`.
      **Brand-level TVL (correctness-critical):** DeFiLlama splits a brand into versioned/product slugs (`aave-v3`,
      `uniswap-v4`, `morpho-blue`), so a direct `defillama.slug = entity.slug` join leaves the BRAND entity
      (`aave`, `uniswap`) with no TVL at all. A `prot_map` CTE resolves every live protocol (`fetched_at > now()-3d`)
      → its canonical entity via: exact slug (any type) → version-stripped base (`-v\d+$`) → brand root (first slug
      token) — branches 2/3 **type-gated to protocol/dao** + a name-prefix check so an exchange/chain root never
      leaks (`binance-staked-eth`→`binance`/exchange and `base-bridge`→`base`/chain are REJECTED, so their brand
      mentions aren't mis-attributed to a peripheral TVL contract). A `fund` CTE then AGGREGATES the protocols up
      to the entity (tvl = Σ, 7d/1d = TVL-weighted mean, category/token/chains from the largest). Result: `aave`
      shows its full ~$12B brand TVL alongside its real 343-mention coverage. Coverage/centrality come straight
      off `entity_memory`/`entity_edges` (the entity IS the spine — no slug-split undercount). Verified: Aave
      343mc/$12.2B, Uniswap 183mc/$3.1B, Hyperliquid 483mc/$6.1B; chains/exchanges/funds/people rank by coverage
      with no TVL; mention_count grounded in real feed citations (575 feed_items cite `aave` in 45d) + edges carry
      headline `examples`.
      The metric/strength controls + type legend hide in this view (it carries its own sector/sort controls).
      **No migration** — `tvl_change_7d`/`tvl_change_1d`/`chains`/`token_symbol` already exist + are populated by
      `app/defillama.py`. `mcap_tvl` is plumbed through both `getEntityGraph` and `getProtocolLeague` but is
      **NULL across the universe** (DeFiLlama's `/protocols` *list* endpoint omits `mcaptvl`), so the Mcap/TVL
      column is intentionally absent and the panel's valuation stat self-hides — it lights up automatically if
      ingest is later given a market-cap source (per-protocol endpoint or a CoinGecko mcap join).

    Nodes = entities; **edges = two entities mentioned in the SAME feed item** (precomputed in `entity_edges`
    by `app/entity_graph.py`, cron 6h). Node **color = entity type** (`--ty-*` palette); size = `mention_count`
    (sqrt); **avatar = real logo → Twitter pic via `unavatar.io/twitter/` → monogram**
    (`lib/entityGraph.avatarCandidates`, ~78% coverage). Active-narrative entities get a **gold halo**.
    **Two edge lenses (`metric` toggle, default Affinity):** *Affinity* = **NPMI** (co-occurrence vs chance —
    surfaces real relationships, ubiquitous hubs like Bitcoin recede); *Volume* = raw co-mention count.
    Strength chips are thresholds on the active metric; edge thickness tracks it too. **The metric + strength
    controls only render in the Network view** (links drive that picture); the Board toolbar keeps just the
    layout toggle, type legend, and search. Edge-click → `EntityMapPanel` shows the affinity score + the
    **actual headlines behind the link** (`entity_edges.examples` = ≤3 {link,ts,snippet}). Controls: layout
    toggle (Board/Network), type legend=filter, metric toggle + strength chips (Network only), search (dims
    non-matches), node-click → **neighborhood focus** + panel (type · mentions · TVL · momentum ·
    a **Fundamentals** block for protocols [`eg-funda`: TVL · 7d/1d flows · chains · token, surfacing the same
    DeFiLlama fields as the Protocols screener] · **Latest Mentions** · top neighbors). The node panel's
    **Latest Mentions** list lazy-fetches the most
    recent feed items naming the entity from `/api/entity-mentions` (POST `{name,slug}` →
    `db.getEntityRecentMentions` word-boundary match on the entity's most distinctive token, ambiguous-name
    crypto-context guard shared with `/api/search`, pure SQL no LLM) — replaced the static `summary` blurb so
    users see live, clickable coverage. Server fetch `db.getEntityGraph({maxNodes:170, minWeight:2})` →
    per-edge `npmi`+`examples`, `unstable_cache(revalidate:3600)`. State lives in `EntityGraph` (no per-node
    React state). URL `?node=<slug>`.
- **Weekly** (`/weekly`) — `WeeklyView`: a list of weekly-report cards (rotation badge · range · preview) →
  `WeeklyPanel` on select.

**Governance** is **no longer surfaced in the web UI** (removed 2026-06-18). It was briefly a header popover
(`GovernanceMenu`, gavel icon + count); that button was dropped from `Header`, and `layout.js` no longer fetches
governance. `GovernanceMenu.js` + `db.getGovernanceProposals` still exist (unwired) for reinstatement; the
`snapshot.py` fetcher + `governance_proposals` table are unaffected (still feed bullet analysis). Header search
(`NavSearch`) works on all views via `lib/useHeaderSearch.js` (`BulletFeed` has its own copy). The `horyon:show-*`
sidebar event bus is gone (search events `horyon:search`/`-loading`/`-done`/`-clear*`/`-focus-search` remain).
Deleted: `Sidebar.js`, `NavMenus.js`, `MobileMenuButton.js`. The narrative-first IA spec (Pulse board, ⌘K) in
`docs/narrative-first-ia.md` was **not adopted**; this redesign keeps the digest-feed feel but drops the sidebar.

## Intelligence pipeline

> 2026-07-07 hardening: a daily **audio-retention** cron NULLs `digest_audio` bytes older than
> `AUDIO_RETENTION_DAYS` (60 — scripts/chapters/metadata kept forever); governance proposals past `end_ts`
> auto-close at each Snapshot fetch (`db.close_expired_proposals`); briefing + entity-brief LLM calls route
> around reasoning models (`llm.complete(..., skip_reasoning=True)` / `config.REASONING_MODELS`); a briefing
> variant whose script can't be produced stores a retryable `status='failed'` row (`blocked` stays terminal);
> narrative momentum recalibrated against the measured ρ distribution (heating ρ≥1.15 ∧ R≥0.5, cooling on
> silent-with-baseline, forming ≤96h/n≤4).

Post-digest, fail-silently, never breaks the digest. Side-effects run through
**`orchestrate_post_digest(html, raw, trigger)`** — sequential, per-step-retry (2×): analyst extraction →
bullet analyses (+ importance scoring) → entity briefs → `db.decay_stale_entities()` → narrative rebuild →
Twitter thread render → og card cache → audio briefing render — all 3 length variants via
`briefing.build_all_variants_for_date` (gated on `AUDIO_BRIEFING_ENABLED`). Each step best-effort.

- **`entity_memory`** — LLM extraction at ingest-time; alias-matched into digest + agent context.
- **`analyst_notes`** — post-digest theme/entity extraction; injected into next digest and agent.
- **`digest_bullet_analysis`** — pre-computed per-bullet analyst views; served instantly in RightPanel via
  `cachedAnalysis` prop, `/api/details` fallback. Ingest cron auto-generates if today's digest has none. Bulk
  regen: `python3 -m app.digest --regen-analyses [--from-date YYYY-MM-DD]`. Carries `importance_score` (0–100),
  `source_count`, `score_breakdown` (JSONB). **Anti-hallucination:** `_generate_one_analysis` detects entities
  (`entities.detect_entities_in_text`) and prepends **two SEPARATE** context blocks — **VERIFIED DATABASE
  FACTS** (live DeFiLlama TVL/7d + Snapshot governance via `db.get_governance_for_entity`, authoritative) and
  **PRIOR ANALYST NOTES** (`entity_memory.summary`, an earlier LLM guess — labeled unverified, must NOT be
  quoted as fact). Kept apart on purpose: merging would launder a past hallucination into the prompt.
  `BULLET_ANALYST_SYSTEM` enforces the verified-vs-hint distinction and bans inventing numbers/dates.
  `get_protocols_by_slugs` must select `tvl_change_7d`. The web `/api/details` fallback mirrors the
  no-fabrication prompt (no DB context → stays qualitative).
- **Importance scoring** (`app/scoring.py`, `compute_importance_scores(bullets, date)`) — runs inside
  `generate_and_store_bullet_analyses` (backfill + regen get it free), best-effort (score=None on failure).
  **Fully deterministic — NO LLM** (the LLM calibration/ranking passes were removed: cost + rate-limit risk for
  little signal). Pipeline: **(1)** 6 positive signals + 1 penalty — s1 corroboration = SUM of source CREDIBILITY
  weights 0–25 (**log-spaced bands recalibrated 2026-07-11 (T1)** against the measured credibility-sum
  distribution: a 30-day replay showed the sum ranges 0…90 while the old bands topped out at ≥3–4, so **83% of
  bullets hit 25 and every daily top-5 bullet scored 25** — s1 did no ranking work. New bands roughly double each
  step: M≥3.0→25, sum≥24→25, ≥12→21, ≥6→17, ≥3→13, ≥1.5→9, ≥0.8→5, ≥0.4→2. **A single premium Kaiko item (3.0)
  still tops the band** via the `max≥3.0` override; **three unknown Tier-2 accounts (sum 3.0) cap at 13**, nowhere
  near max — the republication attack; `source_count` = distinct source **keys**,
  not domains — the old domain count collapsed every Twitter source into one `nitter.net`), s2 financial magnitude
  0–20 (**scans the bullet's OWN text only** — scanning corroborating items imported ambient "$1B" figures from
  adjacent tweets, half the corpus maxed it), s3 appearance velocity 0–15 (**earliest item per DISTINCT source** —
  one account re-posting 5× is not velocity), s4 entity weight (DeFiLlama TVL or `entity_memory.mention_count`)
  0–20, s5 keyword criticality 0–15 (max bucket: hacks/**phishing/stolen/theft/seized**=15; governance +
  **funding/M&A** raise/acquire/merger/**shuts/buys/ipo**=11; launch/upgrade/**debut/listing/testnet**=7;
  partnership=3), s6 novelty vs last 7d **graded 0/2/5** (2026-07-11: was binary 5/0 and a constant +5 for
  195/213 replay bullets — the strict digest pre-filter already dropped hard dups, so `_is_near_duplicate` never
  fired against prior *titles*; now `_is_soft_echo` (Jaccard 0.30–0.45 or ≥2 shared words below the near-dup
  ratio, chain-disjoint titles excluded) adds a middle tier: 5 fresh · 2 soft echo of recent coverage · 0
  near-dup), **s7 saturation PENALTY 0…−12** (subtract when a
  protocol already dominates recent coverage — covered on ≥5 of last 7 digest days → −12, ≥3 → −7; **bypassed**
  when s5≥15 (hack) or s2≥16 (≥$500M) so real news is never buried; counters the rich-get-richer mention/TVL
  loop that let Aave/Morpho/Pendle recur) → `P=max(0,min(100,Σ)−s7)`. **(2)** credibility penalty: ×0.5 when
  the only sources are Tier-3 clickbait. **(3)** temporal decay `max(0.75, 1−age_h/48*0.25)`. Corroboration/velocity
  read a **48h** feed window (was 24h — the cliff zeroed s1/s3 for ~12% of bullets whose story broke the previous
  morning; decay discounts age instead) anchored to the **digest date** (works for backfill). Entity corroboration
  terms pass the shared `entities.matchable_term` gate (junk single-word aliases like "Onchain"/"Notional"
  OR-matched half the corpus); **entity-less bullets** (fallback `_significant_words` terms) only count items
  matching **≥2 distinct terms** — "volume" alone corroborates nothing. **Source credibility** (`FEED_CREDIBILITY`,
  keyed by domain or Twitter handle via `get_source_key`): Tier 1 = 1.2, Tier 2 = 1.0, Tier 3 clickbait = 0.4 —
  the Tier-1 news outlets are keyed **both** as domains and as their Twitter handles (@theblockco etc.; items
  arrive via nitter as handles, so domain-only keys silently downgraded ~80% of their volume). `score_breakdown`
  still carries `llm_adjustment`/`position_bonus` (always 0) for back-compat. Synchronous (all via `db.*`). UI:
  `importance_score` shown as a **flat score badge** (`ScoreBadge` in `BulletItem.js` — tabular tier-coloured
  number over a thin fill bar `width:score%`); `source_count` → "N sources" badge + 1–3 gold dots.
- **`narratives` + `narrative_signals`** (`app/narratives.py`) — the narrative intelligence layer. Full-rebuild
  (post-digest + 3 h cron) clustering cross-source **signals** into persistent narratives with a **momentum
  state**. Sources: `digest_bullet_analysis` + `podcast_episodes.analysis`. **DAO governance excluded by default**
  (`INCLUDE_GOVERNANCE=False` — bursty + obscure flooded the board). Pipeline: gather (30-day window) → resolve
  entities (word-boundary vs `entity_memory`) → Ollama-embed → **greedy cluster** (entity overlap primary, cosine
  support; `BROAD_ENTITIES` (bitcoin/ethereum/usdc + DeFi super-connectors **aave/morpho/pendle**) excluded from
  overlap so no mega-cluster) → board-diversity **entity cap** (≤1 narrative per `SEMI_BROAD` versioned slug,
  and per `DOMINANT_PROTOCOLS` aave/morpho/pendle when that protocol is the cluster protagonist in ≥60% of its
  signals — smaller dup clusters merged into the largest, no signals lost) → **momentum**
  (mass = `importance/100`; R = 48 h mass, B = prior-14d 48h-equiv, ρ=(R+1)/(B+1); forming/heating/steady/
  cooling/dormant, ref-time anchored. **Dormancy is recency-only**: a narrative is `dormant` iff its last signal
  is >168 h old. The single-source-domain anti-manipulation gate only *caps* an elevated state at `steady` —
  it does **not** force `dormant`, so a well-corroborated story whose last signal landed a day ago stays on the
  board even if its sources share a domain. The Pulse board hides `dormant` [`getNarratives` `WHERE state <>
  'dormant'`], so mis-flagging recent narratives dormant was silently emptying the board.) → **LLM synthesis** (label + thesis + `watch_next` + `contrarian`, JSON;
  unchanged clusters reuse prior thesis, capped at `MAX_LLM_SYNTHESES=14`) → `db.replace_narratives` (wipe +
  insert). `narrative_signals` is **denormalized** (title/url/importance/ts). CLI: `python3 -m app.narratives
  [--days N|--no-persist|--no-llm]`. **Real-narrative gates:** must PERSIST, not be a 48 h burst — `WINDOW_DAYS=30`,
  `MIN_SIGNALS=3`, `MIN_SPAN_DAYS=2` (≥2 distinct days). Momentum baseline `BASELINE_HOURS=336`. Synthesis frames
  the thesis as a **developing arc**, not a headline recap. Result: ~9 steady multi-week narratives.
- **`entity_intel_brief`** — pre-computed briefs for **EVERY entity appearing in that day's digest (no
  mention-count floor — `_find_entities_in_text` uses `min_mentions=1`)**. The public web is **zero paid-LLM
  egress**: this brief is the ONLY answer served for an entity (the live-LLM fallback in `/api/search` was
  removed), so coverage must equal the clickable-tag set — hence no floor. Keyed by canonical `entity_name`;
  the web `getEntityIntelBrief` resolves aliases via `entity_memory` and serves the **latest** brief regardless of
  age (no freshness window — a slightly-old brief carrying its `digest_date` beats a live call). Backfill recent
  digests with `python3 -m app.entity_brief --backfill`. Telegram `/updates` (private bot) still falls through to
  the live ReAct agent on a miss. **Reasoning-leak guard:** `_generate_brief` runs output through `_clean_brief`
  (strip `<think>` + preamble before first 🔎/• line) and **rejects** a brief with no `•` (returns None → no
  cache). Before this a reasoning fallback model cached its raw chain-of-thought for ~half the
  entities. `max_tokens=900` so a reasoning model has room to think *and* emit.
  **Format-drift self-heal (2026-07-05):** the model doesn't always follow the "🔎 header / • bullet"
  template literally — it can merge the header + first bullet onto one line via an inline " • " separator,
  or mark continuation bullets with "-" instead of "•". Both are invisible to a reader but broke every
  consumer that only recognized a literal line-**leading** "•" (web `SearchPanel.parseTelegramLines`,
  Telegram render) — an unrecognized line rendered as nothing, not an error, so a drifted brief silently
  looked like an empty search result (measured: 0 of 20 sampled briefs had a compliant bullet line before
  the fix). `_normalize_brief_format` canonicalizes both patterns before storage; `_clean_brief` always
  applies it; `python3 -m app.entity_brief --renormalize` fixes the existing backlog in place with zero LLM
  calls. The web parser also stays lenient as a second line of defense. **Incremental top-up (2026-07-05):**
  `refresh_stale_briefs` runs on a 3h cron between digests — self-limiting: only entities that already have
  a brief (discovery of new entities stays the daily digest's job), whose brief predates the window, AND
  that have ≥2 new anchored feed mentions since then, get regenerated, capped at 15 per run. A quiet news
  cycle costs zero LLM calls. **Market-data facts (2026-07-05):** `coingecko_market` (2h cron,
  `defillama.fetch_and_store_market_data`, top-500-by-mcap CoinGecko snapshot) adds price/mcap/FDV/
  circulating-supply to the brief's VERIFIED DATABASE FACTS block, joined by `gecko_id == entity_memory.slug`
  — the same identity `fetch_and_seed_coingecko` already relies on. This is an exact-slug join, not a
  brand-aggregation one (mirrors the existing DeFiLlama TVL lookup's known gap): ~150 tracked entities match
  directly; an entity whose slug doesn't happen to equal its CoinGecko id gets no market line rather than a
  guessed one. Also surfaced client-side in the Atlas node panel's Fundamentals block
  (`EntityMapPanel.ProtocolFundamentals`, joined the same way in `getEntityGraph`).
  **Search wiring (2026-07-05):** the global header search (`NavSearch`, a DOM CustomEvent bus) only answers
  on a page that mounts `useHeaderSearch`/`useFeedSearch` + renders a `SearchPanel` — this was missing on
  Atlas (`EntityGraph.js`) and Weekly (`WeeklyView.js`), so the search bar silently did nothing there even
  though it worked on Daily/Research. Both now wire it in: Atlas prioritizes the search panel over node
  selection in its existing `feed-right`; Weekly (a deliberate no-right-panel "solo" report) renders the
  panel only while a search is active.
- **`digest_threads`** (`app/threads.py`) — the daily digest as a ready-to-post Twitter/X thread. ONE row per
  date (last step of `orchestrate_post_digest` + CLI). **Intelligence-Brief format: curated TOP-5, NOT one tweet
  per bullet** — `build_thread_for_date` caps `_ordered_bullets` to `OG_CARD_BULLETS` (=5) so the thread mirrors
  the OG card. Shape: a **hook tweet** (the `/api/og` card attaches here) + one ranked tweet per top-5 signal
  (importance desc) + a closing tweet (`cta`). **Each signal tweet is a ranked micro-brief** composed by
  `_compose_brief_tweet`: a `#N · M sources` header (source_count shown only when ≥2) + the grounded development
  (carries entity @tags) + a labeled `Why it matters: …` analyst line. **A single LLM call** rewrites each
  bullet's already-grounded `title`+`body`+`analysis` under a strict **"no new facts"** rule, returning
  **`{i, text, why}` per tweet** (`json_mode`): `text` = purely-factual development (~90–140 chars, NO
  implication); `why` = ONE grounded analyst sentence (≤~85 chars, or empty). The `why` prompt demands the
  SHARPEST specific angle and **bans generic filler verbs** ("improves efficiency", "boosts liquidity", …) — an
  empty `why` beats a bland one.
  - **Budget priority = DEVELOPMENT first, why never clipped:** `_compose_brief_tweet` fits the development FIRST
    (cap `DEV_MAX=170`, sentence-aware `_fit`) but HOLDS BACK `WHY_RESERVE=72` chars; the `why` takes the
    remaining room via `_fit_whole` (cap `WHY_MAX=100`) which **returns '' rather than clip mid-sentence**. The
    `why` is also run through `_clean_why` (drops a generic-boilerplate why with no number/@handle, `_BLAND_WHY_RE`).
  - **Voice is factual-first:** `THREAD_SYSTEM` keeps `text` to development + numbers; the lone implication lives
    in the `why` slot — no "X wins", no predictions, no "what to watch" filler, **no questions** (`?` banned).
  - **Hook is a templated dated masthead:** LLM returns only a factual ≤180-char throughline; `_compose_hook`
    wraps it as `🦅 HORYON DAILY · WED · JUN 17, 2026` + throughline + `Top N signals, ranked 👇`. **Closer**
    (`cta`, `_build_closer`) explains what Horyon is + CTA + `Follow @Horyonhq` (the official X account, NOT the
    old `@HoryonAI`).
  - **URLs are NEVER emitted by the model**; the verbatim source `link` (parsed from digest HTML) is appended in
    code. **Entity @-tagging is grounded:** `entities.detect_entities_in_text` → `entity_memory.twitter_handle`
    builds an allow-list (`_entity_tags`, ≤`MAX_TAGS`); the model weaves handles inline but output is
    `_validate_handles`-validated (strips any @handle not on the bullet's allow-list). **Asset-ticker tags handled
    apart:** an entity mentioned by a TICKER differing from its issuer (USDC→Circle/@circle) is NOT offered to the
    model; the handle is appended after the first occurrence by `_append_asset_tags`, which skips tickers inside a
    pair/compound ("USDC-cbMEGA") or before a Capitalized word ("USDC Vault"). `get_entities_by_slugs` selects
    `aliases` (to find the ticker surface form).
  - **Pre-publish modality gate (fail-closed):** the only outward-facing, auto-posted, irreversible surface, so
    every composed tweet + the hook run through `audit.modality_violations` against `audit.compile_prelaunch_patterns`.
    A tweet asserting a pre-launch entity as LIVE is swapped for `_fallback_tweet`; if the fallback is still unsafe
    (or the hook trips), the whole thread is stored `status='blocked'` and the external poster ships only
    `'pending'`. `db.upsert_thread` takes a `status` arg (default `'pending'`).
  - **Resilience:** best-effort; on LLM/missing-tweet failure a bullet falls back to a deterministic clip of its
    `analysis` (then body) via `_fallback_tweet`. `<think>` leak guard + sentence-aware `_fit`; char budgets:
    hook ≤270, `TWEET_TEXT_MAX`=255. OG card URL uses `config.PUBLIC_BASE_URL`. OG card + web app sit behind Caddy
    basic auth, so the external poster must fetch the image with credentials and upload the bytes. CLI:
    `python3 -m app.threads [--date YYYY-MM-DD|--no-persist|--backfill]`. **Posting is external** — Horyon only
    generates + stores.
  - **Web composer (`/threads`):** the stored thread is previewed + hand-editable — `app/threads/[date]/page.js`
    → `ThreadView` (Twitter-style: falcon avatar, @Horyonhq, connector line, OG card on the hook, per-tweet live
    char count vs 280 incl. the 24-char t.co link, source pill, importance chip). Edit mode → textarea
    (over-budget → red); Save PATCHes `/api/thread` (`db.updateThread`). Per-tweet Copy + Copy all + a Mark
    posted/pending toggle. The OG `<img>` uses a same-origin path so it loads behind basic auth. Persisting an
    edit does NOT re-run the LLM; a later rebuild overwrites hand-edits (resets `status` to pending).
- **`digest_audio`** (`app/briefing.py` + `app/tts.py`) — the daily digest as a spoken briefing in **THREE
  length variants** rendered from ONE set of grounded signals, so a longer/shorter show adds **no** new
  hallucination surface (identical rails + the same modality gate; only verbosity/structure differ).
  Variants (`config.BRIEFING_VARIANTS`): **`short`** (~90-sec **single-host flash**, top
  `BRIEFING_SHORT_MAX_BULLETS`=4 headlines, no chapters, read in the HOST voice), **`standard`** (~6-min
  **two-voice podcast** — female HOST drives + male EXPERT analyst, `BRIEFING_HOST_NAME`/`BRIEFING_EXPERT_NAME`;
  the default sent to Telegram + legacy callers), **`explainer`** (~12-min **two-voice deep dive** — every story
  explained with mechanism + context). Rows are keyed **`(digest_date, variant)`**; the orchestrator and
  `--variant all` render every variant in ONE pass via `briefing.build_all_variants_for_date` (signals enriched
  ONCE for the largest bullet count, then sliced per variant — three shows cost one set of entity/fact lookups).
  Per-variant length/format (target words, max bullets, dialogue on/off, max tokens, `floor`, UI label) lives in
  `config.BRIEFING_VARIANT_SPECS`. **Length FLOOR + cross-variant ordering guard:** a single LLM pass reliably
  undershoots a long word target (a terse model tops out ~1100–1300 words regardless of an "at least 2000" ask),
  so the deep dive could render SHORTER than the standard show on a given day. Two rails fix this: (1) the
  `floor` variants (`standard`/`explainer`, never the flash) enforce a word floor at
  `target_words × BRIEFING_MIN_WORD_RATIO` — a draft under it triggers an EXPAND re-prompt
  (`prompts.build_briefing_expand_user`, re-supplies the grounded signals so the extra length is explanation, not
  invention) up to `BRIEFING_EXPAND_ROUNDS` times, keeping the LONGEST valid draft (`briefing._build_script`);
  (2) `build_all_variants_for_date` renders shortest→longest and raises the explainer floor to at least
  `standard_words × BRIEFING_EXPLAINER_OVER_STANDARD`, so the deep dive can never land at/below the briefing it
  deepens (an explainer-only backfill reads the stored standard's length to do the same). `explainer` also carries
  MORE bullets than `standard` (`BRIEFING_EXPLAINER_MAX_BULLETS`>`BRIEFING_MAX_BULLETS`) so it has extra raw
  material to go deep on. **Mirrors `threads.py` structurally:** reuses `threads._ordered_bullets`, then
  ONE-OR-MORE LLM calls per variant (`prompts.build_briefing_system(host,expert,variant)` +
  `build_briefing_user(...,variant)`) rewrites grounded `title`+`body`+`analysis` into a labeled
  `HOST:`(/`EXPERT:`) script with a real cold open + outro, under a no-new-facts rule (grounding applies to
  EVERY spoken line — the host can't invent figures to ask about). The
  **grounding + write-for-the-ear rails are SHARED VERBATIM** across all three variants
  (`prompts._BRIEFING_GROUNDING` / `_BRIEFING_EAR_RULES`); only the show identity, structure, and length
  guidance change (the `explainer` adds an explicit "depth comes from explanation, NOT invention" rule — its
  length is the highest fabrication risk). The host
  is **didactic** (asks the plain-English "why does this matter / how does it work" question + restates the
  takeaway, varied transitions) and the expert **defines jargon inline**; both **paraphrase, never recite**
  bullets verbatim. Brief CONCEPTUAL explanation from general knowledge is allowed, but every figure/date/event
  must come from the signals. Numbers are spoken short (whole-number percentages, rounded dollar phrases). Each
  signal also carries an `entities` list (`Name (type)`, from `db.get_entities_by_slugs`) used **only** to
  introduce a player ("Aave, the lending protocol") — no figures/events drawn from it. Same
  anti-hallucination rails as the thread: `known_facts` + `audit.prelaunch_warnings` as `VERIFIED FACT` lines,
  PRESERVE TEMPORAL MODALITY, and the same pre-synth modality gate (run on the de-labeled plain text, applied to
  EACH variant independently) — audio **fails CLOSED** (`status='blocked'`, never synthesized/sent; one blocked
  variant never affects the others). Script is **written for the ear** (prompt spells
  tickers → names, numbers → words) + `_normalize_for_speech` safety net (strip markdown/URLs/emoji/list-markers,
  de-dash, **expand crypto acronyms** via the `_SPEAK_AS` map so TTS doesn't garble them — full words where
  natural (`TVL`→"total value locked", `OP Stack`→"Op Stack") or hyphen-phonetic spelling otherwise
  (`MEV`→"em-ee-vee", `USDC`→"you-ess-dee-see"); case-sensitive + word-boundary, extend the map as new jargon turns
  up garbled. The prompt also bans hammering a name (say a ticker once/story then "the stablecoin"/"it") and spelling
  COINED sub-tickers (`cUSDC`, `PT-USDat` → plain words). A deterministic number/ticker net runs in the same pass
  (`briefing._say_numbers`/`_say_tickers`): symbol-form money/percent the LLM forgot → words (`$2.3B`→"2.3 billion
  dollars", `46%`→"46 percent") and bare top tickers `BTC`/`ETH`/`SOL`→names (case-sensitive, tiny set — most
  tickers are common words). The prompt also lets the EXPERT carry a **measured point of view** (thinking out loud,
  calibrated hedges, opinion on *significance* only — never invented facts, never hype) so it reads human, not flat.
  **Continuity:** each signal is enriched with up to
  `BRIEFING_PRIOR_MAX` dated one-liners from earlier digests for its entities (`briefing._prior_coverage` →
  `db.get_prior_bullets_matching_terms`, last `BRIEFING_PRIOR_DAYS`, deduped across the show) so the expert can say
  "this builds on…"; these are already-vetted bullets → grounded. **Chapters:** the script LLM emits a `## Title`
  marker before each story; `_parse_turns` peels them into chapter titles (index 0 = intro), and on the two-voice
  path `synthesize_dialogue` returns per-turn durations so `_build_chapters` maps each chapter to a start-second —
  stored in `digest_audio.chapters` (jsonb `[{title,start}]`) for in-player nav (mono fallback = no chapters).
  `_parse_turns`/`_speaker_turns` split the script into `(speaker, text, chapter_idx)` turns; the stored `script`
  is a named transcript (`Maya:` / `Daniel:`). **TTS is swappable** behind `tts.synthesize(text)` →
  `tts.synthesize_dialogue(turns, voices)` for the two-voice path (returns `(audio, mime, engine, per_turn_durs)`):
  each turn is synthesized with its speaker's Edge voice and the headerless-MP3 streams are concatenated
  (engine/mime must match or it refuses → single-voice fallback). **Edge TTS** (free MS neural voices) is the MVP
  engine — `TTS_HOST_VOICE` (default `en-US-AvaMultilingualNeural`, woman) + `TTS_EXPERT_VOICE` (default
  `en-US-AndrewMultilingualNeural`, man), `TTS_RATE` `+8%` (brisker; numbers are rounded so it stays clear);
  length per variant is driven mostly by its **story COUNT** (`BRIEFING_VARIANT_SPECS[v]["max_bullets"]`) — the
  word target is a floor (or, for `short`, a ceiling) the model tracks loosely. The `short` flash is single-voice
  by spec (`dialogue=False`, read in `TTS_HOST_VOICE`); the two-voice variants auto-fall back to a single-voice
  monologue (`BRIEFING_DIALOGUE=0` or no edge / no expert turn) read by `TTS_VOICE`. Zero local
  compute, native MP3, verified reachable from this Azure IP. Dialogue is **edge-only** (piper is single-voice → auto
  monologue). It rides an unofficial MS endpoint, so `TTS_FALLBACK_ENGINE='piper'` (self-hosted ONNX) is an optional
  fallback. Duration via mutagen's **mime-specific
  class** (`mp3.MP3`, NOT generic `File()` — edge's headerless stream makes `File()` return None); word-count
  fallback (~150 wpm). **Audio bytes stored INLINE as `bytea`**, one row per `(date, variant)` (~1 MB/5-min →
  ~1 GB/yr for all three; travels with DB backups). `status` ∈ pending|ready|blocked|failed. Best-effort
  (run_step isolation + `TTS_TIMEOUT_SEC`=120). Toggle `AUDIO_BRIEFING_ENABLED`. CLI:
  `python3 -m app.briefing [--date|--variant short|standard|explainer|all|--no-persist|--no-audio|--backfill]`
  (`--variant all` default; `--backfill` builds only the MISSING variants per date via
  `db.get_existing_audio_variants`). **Delivery:** web embedded player with a Flash/Briefing/Deep-Dive switcher
  (Daily masthead, served by `/api/audio/[date]?variant=`) + Telegram `send_audio` of the `standard` variant
  after the digest text.
- **`weekly_digest`** — macro report (market + DeFi + 7d news); injects last 3 weekly digests for trend
  continuity; `rotation`: BTC/ETH/ALT/MIXED. DEX weekly volume endpoint 500s on the free DeFiLlama plan
  (handled). **Weekly v2 — sectioned composition (T12, `config.WEEKLY_SECTIONED` default on):** movers (🏆) and
  ROTATION are built DETERMINISTICALLY from exact market data (`weekly.build_movers_block` = exactly 5+5;
  `weekly.compute_rotation`), and the five prose sections (market/defi/trending/stories/watch) are separate
  small LLM completions with shared rails (`prompts.WEEKLY_SECTIONS` + `_WEEKLY_SECTION_RAILS`), each retried
  alone (`weekly.validate_weekly_section`). Both the sectioned and legacy monolithic paths share
  `prompts._weekly_data_blocks` so grounding is identical; assembled output is byte-compatible (same emoji
  headers/order) so `web/lib/weekly.js` is untouched. The single-call path (`WEEKLY_SYSTEM`) is the fallback,
  used on any section failure or overall-validation fail. Em dashes stripped at the data layer
  (`weekly._deslop`) in addition to the prompt ban + web `deDash`. **Cadence (two distinct jobs):** no standalone
  weekly *build* cron. (1) the daily digest cron (07:00
  UTC) fires `_do_weekly_update` → `run_weekly()` for the current in-progress week (`_current_week()`) — a rolling
  preview for the web `/weekly` view, skipped until the week has ≥2 daily digests. (2) `weekly_tg_send` (Mon 07:45
  UTC) sends to Telegram the report for the **week that JUST ENDED** — looks up by *yesterday* (Sunday) via
  `get_weekly_for_date(today-1d)`, NOT today; builds it on the spot if absent. **Historical backfill:** past
  weeks have no live price data, so `WEEKLY_SYSTEM` MUST write "Data unavailable for historical backfill" for
  Market Rotation / Top Movers / DEX + force `ROTATION: MIXED` (a weak model otherwise fabricates). DeFi Pulse is
  the exception: `_build_context` recovers chain TVL from `defillama_tvl` via `db.get_chain_tvl_for_week(week_end)`
  (weeks ≥ 2026-05-29). Verify regenerated historical weeklies for fabricated numbers.
- **`podcast_episodes`** (`app/podcasts.py`) — YouTube crypto-podcast transcript ingestion, no paid API. Cron
  every `PODCAST_INTERVAL_MIN` (6 h). New-episode detection via free per-channel RSS
  (`youtube.com/feeds/videos.xml?channel_id=UC…`); handles resolved to UC ids by scraping (cached per-process).
  Transcripts: `youtube-transcript-api` → `yt-dlp` auto-sub fallback. Per episode: **map-reduce** LLM analysis
  (chunk → per-chunk key points → one structured JSON reduce: `tldr/themes/notable_claims/predictions/entities/
  guests/sentiment`). **Map output sanitized to bullets-only** (`_clean_map_note`: strip `<think>`, keep `- `
  lines, drop instruction-echo via `_META_RE`) — a reasoning-model fallback otherwise leaks chain-of-thought into
  the notes → reduce → a stored field. `_parse_analysis` also strips `<think>` before JSON-parse and meta-filters
  every field. Summary is embedded (768-dim) + run through the shared entity extractor. Recent claims/predictions
  feed the daily digest via `db.get_recent_podcast_summaries(48h)` → `prompts.format_podcast_context`. Channel
  list in `config.PODCAST_CHANNELS` (8 crypto-native channels; unresolvable handles skip non-fatally);
  `config.PODCAST_MIN_DATE` skips old episodes. **Web UI:** podcasts are inline
  daily-news rows — episodes published that UTC day (`lib/db.getPodcastsForDate`) render as `PodcastFeedItem`,
  filterable via the Podcasts chip; click opens `PodcastPanel`.
- **`podcast_predictions`** (T14, `app/podcasts.py`) — forward-looking claim tracking, the only place the system
  follows up on predictions. Filled at summarize time (`_store_predictions`) from the existing reduce pass's
  `predictions` list, with each claim's entities resolved by the shared vetted detector (no new LLM path). A
  monthly DETERMINISTIC recheck (`recheck_predictions`, 1st-of-month 06:30 UTC cron + `--recheck-predictions`
  CLI) word-boundary-matches each open prediction's entities against LATER digest coverage
  (`db.get_digest_bullets_matching_since`) and sets `outcome` = `corroborated` (coverage appeared, with
  `evidence` [{date,title}]) or `stale` (past `PODCAST_PREDICTION_STALE_DAYS` with none). It is an honest
  "coverage appeared" signal, NEVER an LLM verdict on whether the call was right.
  `db.get_predictions_for_research` is the (as-yet-unwired) reader for a Research-page "called it" module.

**Dedup** (daily digest): URL pre-filter (drop items whose URL appeared in last 7d digests) + Python post-filter
(normalized title fingerprint: first 6 significant words, lowercased, emoji-stripped) + `covered_bullets`
exclusion list injected into the prompt. `backfill_digests.py` fetches dedup context fresh per date.

Fully standalone — migrated off n8n. Do not reintroduce n8n tables, containers, or coupling.

## Repo layout

### `app/` — Python bot (baked into image, no volume mount)

- `main.py` / `config.py` / `handlers.py` — PTB entrypoint, env config (never read `os.environ` elsewhere),
  Telegram router. Startup jobs are staggered (`next_run_time` offsets — see Conventions).
- `digest.py` — daily digest: entity + TVL + dedup context → LLM (temp 0.5) → keep **only `•` bullet lines**
  (`_keep_bullets_only`, anti prompt-echo) → post-filter duplicates → persist the cleaned bullets. **Empty-guard:**
  0 parseable bullets → retry once, else raise. `_keep_bullets_only` also **de-slops** each bullet body
  (`_deslop_bullet_body`): preserves the `<b>Title</b> — ` separator but converts em/en dashes inside body prose
  to commas. `_DIGEST_RULES` forbids in-body em dashes + mandates spelling out ambiguous tickers that are English
  words (THE=Thena, ID, ARC, ARB), and enforces **DIVERSITY** (≤1 bullet per protocol, spread across sectors).
  Scope = onchain DeFi **plus crypto/web3 funding rounds + acquisitions** (the one allowed non-onchain category,
  high priority; ETF/TradFi/treasury items still HARD-DISCARDED).
  `build_digest` also injects an **OVER-COVERED PROTOCOLS** block (`_saturated_entities`: protocols covered on
  ≥3 of the last 7 digest days) telling the model to apply a higher bar to those — only hack/governance/launch/
  big-flow stories clear it, routine yield/rate/TVL updates are dropped (thins the Aave/Morpho/Pendle drumbeat).
  `_format_items` reads each item's `quality_flag` and appends a per-item `NOTE:` for `thin_content` (report
  ONLY what TEXT states, never pad a teaser) and `boilerplate_title` (multi-story newsletter recap — not a fresh
  event); the flag comes back from `get_recent_feed_items`/`get_feed_items_for_date` ('ok' pre-migration). **⚠️ EVERY digest-persist path MUST apply `_keep_bullets_only` + a `"•" in
  body` guard** (else a reasoning fallback model leaks chain-of-thought into the stored digest). Bullet analyses
  run at `max_workers=3`. Post-digest side-effects → `orchestrate_post_digest` (see pipeline above).
- `scoring.py` — bullet importance scoring (0–100) + `source_count`: 6 positive signals + s7 saturation penalty
  → credibility penalty → temporal decay. NO LLM. See pipeline above.
- `specialized.py` — ReAct agent with `search_feed` tool; checks `entity_intel_brief` cache first.
  `search_feed` excludes `thin_content` rows and the injected analyst notes carry the shared
  `prompts.ANALYST_NOTES_LABEL` provenance label (model-generated, unverified).
- `entity_brief.py` — post-digest entity brief generation: SHARED-detector match in digest text
  (`entities.detect_entities_in_text` — same false-positive gates as every other surface) → up to 5 parallel
  LLM calls → upsert `entity_intel_brief`. A per-entity `_entity_anchor` pattern (name + distinctive aliases,
  ambiguous single-word brands case-sensitive) gates today's bullets, the 14-day history and the `search_feed`
  results, so an unanchored ANN hit can't attribute another protocol's event to the entity (fail-open on error).
- `entities.py` / `analyst.py` — entity extraction (ingest-time LLM) + analyst scratchpad (post-digest theme
  extraction, digest chain + notes formatter).
- `known_facts.py` — **curated, human-authored ground-truth facts** about entities the LLM keeps getting wrong
  (the durable anti-hallucination lever). `KNOWN_FACTS` (slug→fact) + `TRIGGER_TERMS` + `facts_for_slugs`/
  `facts_for_text`/`block`. Injected as a top-priority **AUTHORITATIVE KNOWN FACTS** block into **all SIX LLM
  write-paths** — digest, bullet-analysis, thread, weekly, entity-brief (`build_entity_brief_user`),
  analyst-extraction (`build_analyst_extraction_user`, which writes `entity_memory.summary`). OVERRIDES source
  framing AND the LLM-written summary. Seeded with **Arc** (Circle's unreleased testnet L1). Add an entry when a
  source-vs-reality gap causes a *recurring* error; keep dated, prune when stale. **Run `app.audit` after seeding**.
- `audit.py` — **stored-data integrity auditor** (retrospective complement to `scripts/llm_quality_audit.py`).
  Scans every digest bullet / analysis / thread / weekly / narrative / entity_intel_brief / `entity_memory.summary`
  for MODALITY / OVERSTATE / THREADLINK / ENTITY issues. `python3 -m app.audit` reports (exit≠0 on hard issues);
  `--fix` does only safe self-healing structural fixes. Also the source of the shared modality logic
  (`prelaunch_entities()`, `compile_prelaunch_patterns()`, `modality_violations()`, `prelaunch_warnings()`) reused
  by the thread/audio gate. Import `audit` **lazily inside functions** (pulls `entities` → `prompts` → cycle). See
  Conventions › Auditing stored data.
- `ingest.py` — fetch → clean → insert → embed → entity extraction (only when `inserted > 0`).
- `kaiko.py` — **non-RSS source** for Kaiko Research (kaiko.com). Kaiko exposes no RSS feed or public REST
  API (headless WordPress behind a Nuxt frontend), so this discovers article URLs from the Yoast post-type
  sitemaps (`new-`/`resource-`/`report-sitemap.xml`) and pulls `title`/`summary`/`datePublished`/breadcrumb-
  category from each page's JSON-LD + OpenGraph meta. Only URLs not already in `feed_items` are fetched
  (`db.existing_links`), bounded per run (`KAIKO_MAX_FETCH_PER_RUN`); a breadcrumb-category gate drops
  marketing collateral (client stories, product/use-case/methodology pages). Items route through the shared
  `ingest.clean_items`/`sanitize_items` and land in `feed_items` as `source_type='news'`/`creator='Kaiko'`,
  so they flow into the digest, narratives, entity graph, and search like any RSS news source — no extra
  wiring. 6 h cron (`KAIKO_INTERVAL_MIN`), CLI `python -m app.kaiko [--no-persist|--limit N|--backfill]`.
  Stdlib-only HTTP (urllib) — no new dependency. Toggle with `KAIKO_ENABLED`.
- `weekly.py` / `coinmarketcap.py` / `defillama.py` — weekly macro, market data (CMC if key set, else CoinGecko
  free), chain TVL daily + protocol TVL every 2h + CoinGecko top-500 logo seed.
- `snapshot.py` — Snapshot DAO governance via GraphQL; top 500 verified spaces cached 24 h in-process; up to 50
  active proposals sorted by soonest ending; upserts every 30 min.
- `podcasts.py` — YouTube transcripts → map-reduce LLM analysis → `podcast_episodes`; also fills
  `podcast_predictions` + the deterministic `recheck_predictions` follow-through (T14). CLI: `python -m
  app.podcasts [--resolve @handle | --no-persist | --limit N | --recheck-predictions]`.
- `eval_harness.py` — **scored LLM grounding + prompt-injection eval (T10/T11)**. Each write-path (digest,
  analyst, brief, thread, briefing, narrative) runs against a FIXED synthetic fixture; output scored by
  deterministic checks (`urls_grounded`, `numbers_grounded` via `narratives._significant_numbers`,
  `modality_ok` via `audit.modality_violations`, `format_parses` by the path's own consumer, `no_ai_isms`,
  digest `no_body_em_dash`). A permanent injection case set (`INJECTION_CASES`: override + `PWNED-CANARY-93`,
  format-escape, fact-plant, link-swap) runs against the three raw-text paths and asserts `canary_absent` +
  `planted_urls_absent` + modality holds. Rows persist to `eval_runs` (shared `run_at`); weekly cron
  Sun 20:10 UTC + on demand. URL primitives (`URL_RE`/`url_key`/`url_keys`) single-sourced here and imported by
  `scripts/llm_quality_audit.py`. CLI: `python3 -m app.eval_harness [--path P|--case C|--no-persist]`. One run
  at a time (process-local LLM limiter).
- `narratives.py` — narrative intelligence layer. CLI: `python -m app.narratives [--days N|--no-persist|--no-llm]`.
  `_EntityMatcher` gates every term through `entities.matchable_term`.
- `entity_graph.py` — entity co-occurrence precompute for the Entity Map. Scans the last `ENTITY_GRAPH_DAYS` (45)
  of feed items with one word-boundary alternation regex (`entities.matchable_term` gate), counts entity PAIR
  co-occurrence, stores per edge `weight` (raw), `npmi` (∈[-1,1], down-weights ubiquitous hubs), `examples` (≤3
  {link,ts,snippet}), `last_seen` → `entity_edges` via `db.replace_entity_edges`. Cron 6 h. CLI: `python3 -m
  app.entity_graph [--days N|--min-weight W|--no-persist]`. `get_feed_items_since` must select `link`. ~18k items
  → ~573 nodes / ~4.9k edges (w≥2) in ~45 s.
- `threads.py` — daily digest → Twitter thread. See pipeline above.
- `briefing.py` — daily digest → ~5-min two-voice audio podcast. See pipeline above.
- `tts.py` — swappable TTS interface. `synthesize(text)` single-voice; `synthesize_dialogue(turns, voices)`
  stitches per-speaker Edge voices into one MP3. Edge TTS (default) → optional Piper (`TTS_FALLBACK_ENGINE`).
  `estimate_duration` via mutagen's mime-specific class. edge-tts runs its async coro on a private loop in a
  worker thread (`_run_async`) with a hard timeout.
- `llm.py` / `prompts.py` — **multi-provider LLM client**: `_model_chain()` = NVIDIA NIM (`NIM_MODELS`, if
  `NIM_API_KEY` set) **then** OpenRouter (`OPENROUTER_MODELS`); `_chat()` tries each in order, retries brief
  5xx/conn blips once, falls through on any error, per-request timeout `LLM_TIMEOUT_SEC` (60s; callers may
  override per request — `complete(..., timeout=)`, used by briefing scripts with
  `BRIEFING_LLM_TIMEOUT_SEC=180` since a 7200-token draft can't finish in 60s). `complete(system,
  user, max_tokens, temperature=None, json_mode=False)`: low temperature for JSON/format (~0.1–0.3), ~0.5 for
  prose; `json_mode=True` sends `response_format=json_object` (**never for array-returning prompts**).
  `parse_json_loose()` extracts the first balanced `{…}`/`[…]`; on a truncated response it **salvages** the
  parseable prefix via `_salvage_json`. `strip_think()` is the shared `<think>`-block strip primitive every
  persist path runs before its own content guard. **all prompts live in `prompts.py` only**;
  `complete()`/`run_agent()` return `(content, model_used)`.
- `util.py` — shared stdlib-only pure helpers (`plain_text`, `fmt_usd`, `decode_entities`, `as_utc`,
  `TAG_RE`/`WS_RE`, `strip_foreign_hrefs`/`norm_link` — the outward-surface href allowlist used by the digest +
  entity brief to unwrap planted/hallucinated links, 2026-07-16); replaced per-module copies 2026-07-15 (see
  Conventions "Shared pure primitives"). Contract suite `tests/test_util.py`.
- `db/` (package) / `embeddings.py` / `memory.py` / `feeds.py` / `telegram_html.py` / `monitor.py` /
  `backfill_digests.py` — DB pool + all CRUD, Ollama embed (768-dim), per-chat history, ~99 feed sources, HTML
  sanitizer + 4096-char splitter, Flask dashboard, historical digest + analysis backfill.
  **`db/` is a domain-split package** (it grew past 1.7k lines as one file): `_core` holds the connection pool,
  the `_conn` context manager, and the `_fetchall`/`_fetchone`/`_execute` helpers; the domain modules
  (`feeds`, `digest`, `protocols`, `entities`, `analysis`, `threads`, `audio`, `weekly`, `governance`,
  `podcasts`, `narratives`, `evals`) each own their tables' queries and import only from `_core`. `db/__init__.py`
  re-exports every public function (and `_conn`) flat, so callers keep doing `from . import db` → `db.foo()`
  with no change. Put a NEW query in the module that owns its table; don't recreate a monolithic `db.py`.
  **`monitor.py` Pipeline-health panel (T9):** `_pipeline_health()` surfaces the silent-degradation states
  (unembedded-after-45min, sources at `consecutive_failures > 10`, audio not-ready/incomplete-day, governance
  frozen-active, `digest_audio` MB + retention-stalled, and the `eval_runs` trend). New tables: **`eval_runs`**
  (T10 scored eval, bot-only) and **`podcast_predictions`** (T14, bot-only) — neither is web-read, so no
  `web_db_role.sql` grant.

### `web/` — Next.js 14 (baked into image)

Sidebar-free top-nav IA; header view switcher over routes, each a `.feed-left` list + shared `.feed-right`
RightPanel. `.reader` is the fixed full-width shell.

- `app/layout.js` — SSR root: no data fetch; renders `Header` + `<main class="reader">`. No sidebar, no global
  prefetch (each route fetches its own). **PWA (T15):** declares `manifest: "/manifest.webmanifest"`
  (`app/manifest.js`, standalone display + brand colors + `icon-192/512/maskable-512.png`), `appleWebApp`, and a
  `viewport.themeColor`; mounts `<ServiceWorker />` (registers `public/sw.js`). The SW is installability + an
  offline fallback ONLY: it NEVER intercepts `/api/*` (freshness + zero-egress) and NEVER caches app HTML (the
  per-request nonce'd CSP would replay a stale nonce), so the offline fallback is the static, script-free
  `public/offline.html`; only `/_next/static/*` + hashed assets are cache-first. Registrar runs from a nonce'd
  bundle → no CSP change.
- `app/page.js` (`/`→latest) / `app/d/[date]/page.js` — daily server component: parse digest, build project hints,
  fetch bullet analyses + TVL + market + bullet source times (`getBulletTimes`→`ts`) + the digest list
  (`listDigests`→`items` for `DateNav`) + that day's podcasts (`getPodcastsForDate`) + audio-briefing metadata
  (`getAudioBriefing`→`audio`, now `{variants:[…]}` — ALL ready length variants ordered short→standard→explainer).
  The masthead lives in `BulletFeed` (filters to ready variants → `audioVariants`, passes to `AudioPlayer`).
- `app/components/AudioPlayer.js` — discreet daily-briefing player (client): masthead strip backed by native
  `<audio>` (`preload="none"`), play/pause + seek + `1×/1.25×/1.5×/2×` rate (persisted in `localStorage`).
  Takes `variants` and renders a **length switcher** (Flash / Briefing / Deep Dive pills) when >1 is ready;
  `src` = `/api/audio/[date]?variant=<selected>`. Switching a variant pauses, `load()`s the new source, resets
  the clock to that variant's known duration, reapplies the rate, and resumes if it was playing (each variant
  carries its own `duration_sec` + `chapters`). **Chapters** (per variant, jsonb `[{title,start}]`; the `short`
  flash has none): tick marks on the track, the active chapter title shown in place of the label, and a toggle
  (`☰`) opening a clickable jump list. **Chapter deep links (T15):** each chapter row has a 🔗 copy button
  (`audio.chapterDeepLink` → `/d/DATE?variant=V&t=SEC`); `AudioPlayer` reads `?variant=&t=` on mount
  (`parseAudioDeepLink`) and `AudioProvider.requestSeek` seeks there (deep link wins over the saved position,
  switching length first if named). `useMediaSession` lock-screen artwork is the per-date OG card
  (`/api/og?date=`) + square icon fallbacks. `app/api/audio/[date]/route.js` = **Range-aware** stream of the
  `digest_audio.audio` bytea for `?variant=` (default `standard`; validated against the allowed set) — only
  'ready' served; works behind basic auth via same-origin cred replay.
- `app/narratives/page.js` → `NarrativeView`; `app/weekly/page.js` → `WeeklyView` — the two board routes.
- `app/components/BulletFeed.js` — daily view root: owns selected + cursor + panelOpen + sortBy + `active`
  source-filter state. Renders the masthead (`DateNav` + signals/ago meta + `SortToggle` + `SourceFilter`) and a
  unified `entries` memo of news bullets (`BulletItem`) + podcast rows (`PodcastFeedItem`, `kind:"podcast"`),
  filtered by `active[srcType]` then sorted (importance / `ts` desc, stable). Selection is position-based → reset
  on sort/filter change. Owns search via `horyon:search`/`-clear-search` events.
- `app/components/DateNav.js` — date-stepper masthead: ‹ / › step to prev/next digest day (Links over
  `listDigests`), date label opens a month calendar popover, `[`/`]` keys step days. `.datenav-full` stays visible
  on mobile (≤540px the `.datenav` takes its own full-width row).
- `app/components/SourceFilter.js` — All · News · Tweets · Podcasts chips. `PodcastFeedItem.js` — podcast feed row.
- `app/components/BulletItem.js` — bullet row + `InlineTags` (entity tags with avatar + category chips; label
  click → search; hover shows ↗ external link). **Severity bar** (`classifySeverity`, word-boundary regex,
  `.bullet-sev--{red|gold|green|neutral}`): red = security, gold = governance, green = growth, neutral = default;
  resting-state only. **Top-right meta cluster:** ONE top-aligned `.bullet-aside` stack — `ScoreBadge` then per-
  bullet time + corroborating-source count (`.bullet-meta`). **Per-bullet time** = `timeAgo(ts)` from
  `feed_items.pub_date` (`suppressHydrationWarning` on `<time>`). **Source dots:** `sourceDotCount` → 2 dots
  (2–3) / 3 dots (4+), renders at `sourceCount ≥ 2`. **Denser list:** `.bullets` is `gap:0` with a hairline
  divider (`.bullet::after`); footer split into entity pills (`.bullet-inline-tags`) then a quiet category line
  (`.bullet-cats`, middot-separated uppercase mono, capped at 2). **Every entity tag carries an image:**
  `EntityAvatar` walks an `avatars` candidate chain and on full failure draws a type-coloured monogram
  (`.entity-mono--{type}`). `?fallback=false` makes unavatar 404 (vs grey blob) so the monogram wins.
  `lib/projects.js` builds the entity_memory `avatars[]`; `buildEntities` builds chain/protocol ones + `pickBetter`
  prefers a candidate that *has* avatars.
- `app/components/RightPanel.js` — shared detail surface for all views: AI analyst panel, **Sources** (every
  outlet that reported the bullet — same population behind the `source_count` badge, via `/api/sources`; cited
  link flagged), protocol/chain TVL cards, WeeklyPanel (tabbed), NarrativePanel, PodcastPanel, Related Stories +
  SearchPanel. Precedence: search → narrative → podcast → weekly → bullet → empty.
- `app/components/NarrativeView.js` / `WeeklyView.js` — the two board routes' client roots: master list of cards
  + shared `RightPanel`; auto-select first; header search via `lib/useHeaderSearch.js`.
- `app/components/EntityGraph.js` — the `/map` co-occurrence-map root (client; owns ALL graph state). Two
  layouts behind a `view` toggle (default `board`): a **deterministic carded Board** (no physics — pure
  grid/treemap, frames itself to fit) and a **force-directed Network** (D3 sim run imperatively, drag-able).
  React owns chrome + `<svg>`; D3 owns the canvas via `d3-selection` data-joins — filter/search/select only
  re-style, never relayout/restart physics. `EntityMapPanel.js` = its detail drawer.
  `lib/entityGraph.js` = shared `TYPE_META`/`TYPES` taxonomy (avoids a circular import). Map CSS + `--ty-*` palette
  live in `globals.css`. (`NarrativeViewToggle.js` removed 2026-06-19 when the map became its own top-level route.)
- `app/components/ThreadView.js` — the `/threads` composer client root. See the `digest_threads` web-composer
  entry above.
- `app/components/MainNav.js` — header view switcher (Daily · Narratives · Entity Map · Weekly), `usePathname`
  active state. (Thread `/threads` is a live but unsurfaced route; `GovernanceMenu.js` exists but is no longer rendered.)
- `app/components/MomentumChip.js` — momentum arrow (from ρ) + delta badge + state glyph.
- `app/components/Header.js` / `ThemeToggle.js` — server header (brand · `MainNav` · centered `NavSearch` · theme
  toggle) + client islands. In-flow flex; mobile reflows via explicit `order`. FOUC dark/light via inline
  `<script>`. (Deleted: `Sidebar.js`, `NavMenus.js`, `MobileMenuButton.js`.)
- `app/api/search/route.js` — POST `{keyword, entity, mode}`. **`entity:true` = a clicked entity tag; `false` = a
  free-text search-bar query.** **ZERO PAID-LLM EGRESS** (public site): the ReAct agent + the one-shot synth call
  were removed so an unauthenticated visitor can never burn the NIM/OpenRouter budget — answers are PRECOMPUTED or
  rendered deterministically. An entity tag click is still **two-phase + progressive** (`BulletFeed.handleSearch`
  fires both, renders feed first): `mode:"feed"` = `entityFeedRows` (word-boundary match on the entity's most
  distinctive token) → `formatFeedBullets` → Telegram HTML; `mode:"synth"` = the pre-computed `entity_intel_brief`
  if it exists (now serving the latest, no freshness window + alias-resolved), **else the same deterministic
  `formatFeedBullets`** (no LLM). Free-text (entity=false) = pre-computed brief if the query resolves to an entity,
  else `feedAnswer`: **Postgres full-text search** (`searchFeedRows` — `feed_items.content_tsv` GIN index,
  `websearch_to_tsquery`, `ts_rank` blended with a 14-day recency decay, top-15/30d) → `formatFeedBullets`, with a
  word-boundary fallback (`entityFeedRows`) when the query reduces to an empty tsquery or FTS returns nothing.
  **ZERO per-request external calls now** — the old per-query Ollama embed was replaced by in-DB FTS, so the route
  is pure SQL and scales to high QPS with no single-host dependency. Keyword capped at 120 chars. (Removed: `chatCreate`/`chatComplete`,
  `SYSTEM`/`SYNTH_SYSTEM`/`SEARCH_TOOL`, `synthesizeEntity`, `buildEntityContext`/`buildAnalystNotes`, the 8-step
  loop. The private Telegram bot keeps the live ReAct agent in `specialized.py`.)
- `app/api/suggest/route.js` — GET `?q=` entity **typeahead** for the search bar. Builds an in-memory index of
  `entity_memory` (name + aliases, `mention_count ≥ 2`, type ≠ `other`) tagged with a `hasBrief` flag from
  `entity_intel_brief`, refreshed on a 5-min TTL behind a single-flight guard. Every keystroke is an in-process
  prefix/substring filter (zero DB round-trip, zero external call) — ranks prefix > substring, brief-backed first,
  then `mention_count`; returns top-8 `{name, type, hasBrief}`. NavSearch debounces it (140 ms, ≥2 chars) and
  selecting a suggestion routes through `/api/search` free-text (which resolves the brief). Stop-list mirrors
  `TAG_STOPWORDS` so a generic alias is never offered standalone.
- `app/api/details/` — per-bullet analyst context, **no live LLM**: looks up the precomputed
  `digest_bullet_analysis.analysis` by title (latest `digest_date`); a genuine miss returns empty so the panel
  shows "no additional details". (Was a `lib/llm.chatComplete` call; the precomputed analysis is served to the
  panel via the `cachedAnalysis` prop and this route is only the prop-absent fallback.)
- `app/api/related/` / `find-digest/` — related stories (last 30d, pure SQL) + weekly Key Stories lookup.
- `app/api/og/route.js` — **social image generator**: `GET
  /api/og?date=YYYY-MM-DD&type=daily|weekly|markets|alpha|defi&bullets=N`. Returns a **1200×628 PNG — Twitter
  `summary_large_image` 2:1**. Layout: header (falcon · HORYON wordmark · gold DM-Mono date · muted subtitle) →
  gold bar → **hero** (top-scored bullet: eyebrow "TODAY'S TOP SIGNAL", gold accent border, 34–54px single-line
  headline, inline category badge, multi-line factual detail from `body`, up to 3 lines) → hairline → **signal
  list** of next bullets (category-color stripe, single-line headline + multi-line detail, category badge) → gold
  rule → footer (`FULL INTEL AT HORYON.AI →` + `+ N more signals today`). **Bullets ranked by `importance_score`**
  (`db.getBulletAnalyses`), capped at `bullets` (default 5, clamp 1–5). Each row carries a SUBSTANTIVE factual
  explanation. Details use `fitSentences()` (keeps whole sentences); a CSS `-webkit-line-clamp` is the hard
  visual bound. Headlines `clip()`'d (no visible `…`). Category = `detectCat`. Falcon at ~3% opacity. Uses
  `next/og` (satori). Fonts (Raleway 800/700/500, DM Mono 400) + falcon cached module-level. **Font gotcha:** see
  Conventions.
- `lib/db/` (package) — all Postgres queries, **split by domain** (it grew past 900 lines as one file):
  `_core` holds the shared `pool` plus the `safeRows`/`safeOne`/`iso` helpers; domain modules `digest`, `threads`,
  `audio`, `tvl`, `governance`, `podcasts`, `weekly`, `analysis`, `entities` (matching/search + intel briefs +
  `resolveEntities` + `TAG_STOPWORDS`/`AMBIGUOUS_TERMS`/`CRYPTO_CTX`), and `narratives` (board/inspector +
  `getEntityGraph`) each own their tables' queries and import only from `_core` (narratives also imports
  `resolveEntities` from `entities`). `db/index.js` re-exports the flat API (`export *` per module + `pool`) so
  every `import { … } from "../lib/db"` is unchanged. Notables: `getEntityIntelBrief`, `getBulletAnalyses`,
  `searchEntityMemory`, `searchProjectInfo`, `getTvlWithChange`, `getEntityLeague` (Atlas Index screener — all
  entities, brand-aggregated TVL), `getNarrativesWithSignals`, `getEntityGraph` (nodes now carry `tvlChange7d`/
  `tvlChange1d`/`mcapTvl`/`chains`/`tokenSymbol` for the panel Fundamentals block).
  `latestDate()` filters `error IS NULL AND content <> ''` so a failed run never hijacks `/`. Stop-lists
  consolidated into one shared `TAG_STOPWORDS`, single-sourced in `lib/tagStopwords.js` since 2026-07-15 and
  imported by both matcher layers (see Conventions). **Add a new query to the module that owns its
  table, not a monolith.** `lib/useHeaderSearch.js` — shared header-search hook.
- `lib/llm.js` — multi-provider LLM client mirroring `app/llm.py` (`chatCreate`/`chatComplete`).
- `lib/narratives.js` — pure presentation helpers (state/momentum/type/severity meta, `timeAgo`,
  `evidenceCounts`).
- `lib/digest.js` / `lib/projects.js` / `lib/prices.js` — HTML bullet parser; project hints builder (DeFiLlama +
  entity_memory; per-entity `avatars[]` candidate chain); CoinGecko prices (5-min revalidate).
- `lib/format.js` — shared display formatters (`fmtTvl` compact USD, `fmtAgo` short relative time). ONE
  definition per format so every surface renders identically; import these instead of re-defining a local copy.
  Surface-specific variants that intentionally differ (e.g. RightPanel's `$1.2K` price, EntityMapPanel's
  day-granularity `fmtDayAgo`) stay local on purpose.
- `app/components/icons.js` — shared inline SVG icons (`XIcon`, `ExtIcon`). Pure presentational; import rather
  than pasting another `<svg>`.

### `deploy/`

`schema.sql` (authoritative for fresh DB volumes) · `Caddyfile` · `landing/` (static marketing site served
at the apex).

**Routing (Caddy).** Apex `horyon.xyz` serves the static marketing landing (`deploy/landing/`,
bind-mounted at `/srv/landing:ro`, hand-written `index.html`/`styles.css`/`theme.js` brand-matched to the
app, its own static CSP) plus `/tg/*`→bot and `/monitor*`→monitor (basic-auth), and 301-redirects the old
apex app paths (`/d/*`, `/narratives`, `/map`, `/weekly`, `/threads`, `/api/*`) to `app.horyon.xyz` to carry
over SEO equity. `app.horyon.xyz` fronts the Next product (`web:3000` — read site + `/api/*`, with the
`/threads` composer basic-auth gated). `www.horyon.xyz` 301s to the apex. The Telegram webhook stays on the
apex (`/tg/*`), so `TELEGRAM_WEBHOOK_BASE` is unaffected by the app move. **`app.horyon.xyz` requires a DNS
A record** pointing at the host before deploy (Caddy auto-provisions its cert on first request).
