# Architecture

Data flows and component structure for the crypto-intelligence bot.

## Intelligence layer

```
── Ingest (every 20 min, only when inserted > 0) ────────────────────────────
ingest.run_once()
  → insert_feed_items() → RETURNING link → inserted_links set
  → embed_missing()
  → entities.extract_and_upsert_entities(new_items)   ← only genuinely new items
      parses @mentions from feed text → appended to LLM snippets
      LLM call (max_tokens=2000): texts → [{slug, name, type, aliases, twitter_handle}] JSON
      ERC-XXX / EIP-XXX names dropped before upsert
      → db.upsert_entity() → entity_memory (aliases union-merged, mention_count++)

── Digest (daily 07:00 UTC + manual) ───────────────────────────────────────
digest.build_digest()
  ① entities.build_entity_context(feed_items)
       alias map from entity_memory → matched slugs (free, no LLM)
       → TVL + summary from defillama_protocols + entity_memory
       → "ENTITY CONTEXT" block (top ENTITY_CONTEXT_LIMIT entities)
  ② analyst.format_analyst_notes()   → last ANALYST_NOTES_DAYS days of notes
  ③ analyst.format_digest_chain()    → last DIGEST_CHAIN_DAYS past digests
  ④ build_digest_user(tweets, entity_context, digest_chain, analyst_notes, tvl_context)
  ⑤ llm.complete() → raw digest

digest.run_digest()
  → build_digest() → persist_digest()
  → analyst.extract_and_persist(raw)   ← non-blocking, logged on failure
       LLM call (max_tokens=800): digest → {"notes": [...], "entity_updates": {...}}
       → analyst_notes row  +  entity_memory.summary updates per entity
  → generate_and_store_bullet_analyses(date, html)  ← up to 5 parallel LLM calls
       → digest_bullet_analysis rows (title → analysis)

── Agent (per query) ────────────────────────────────────────────────────────
specialized.run_specialized(query)
  → entities.build_entity_context_for_query(query)
  → analyst.format_analyst_notes()
  → system = SPECIALIZED_SYSTEM + tvl_ctx + entity_ctx + notes_ctx
  → llm.run_agent(tools=[search_feed])
```

## DeFiLlama data flows

```
── Chain TVL (daily 07:10 UTC) ──────────────────────────────────────────────
defillama.fetch_and_store()  →  defillama_tvl (UNIQUE date+chain)
  → prompts.format_tvl_context() injected into every digest + agent call
  → web/lib/db.js::getTvlSnapshot()   → Sidebar TVL panel
  → web/lib/db.js::getTvlWithChange() → digest page TvlStrip
  → monitor.py "DeFi TVL" section

── Protocol TVL (every 2h) ──────────────────────────────────────────────────
defillama.fetch_and_store_protocols()  →  defillama_protocols (UNIQUE slug, top 1000)
  includes chain_tvls JSONB (prefers currentChainTvls, falls back to chainTvls)
  → entities.build_entity_context(): TVL + 1d change per matched entity
  → web/lib/projects.js::buildProjectHints() → RightPanel chain distribution bars
  → monitor.py "DeFi TVL" section

── CoinGecko entity seed (manual / periodic) ────────────────────────────────
defillama.fetch_and_seed_coingecko(top_n=500)
  → /coins/markets top 500 by market cap (2 pages × 250, sleep 2s between pages)
  → db.upsert_entity_from_coingecko() per coin
       INSERT  mention_count=1 (below display threshold — invisible until organic mention)
       CONFLICT: only fills missing logo_url + merges aliases; never touches type/mc/handle
  Run via: docker exec horyon-bot python3 -m app.entity_audit seed-from-coingecko
```

## Weekly macro digest

```
── Weekly macro (Monday 07:30 UTC + manual) ─────────────────────────────────
weekly.run_weekly()
  → coinmarketcap.get_market_data()   (CoinGecko fallback if no CMC_API_KEY)
  → defillama: chain TVL + protocol TVL context
  → db: last 7d feed items (news context)
  → db: last 3 weekly_digest rows (trend continuity, dedup via WEEKLY_SYSTEM prompt)
  → llm.complete(WEEKLY_SYSTEM, user_prompt) → HTML content
  → db.upsert_weekly_digest(week_start, week_end, content, rotation)
       rotation = BTC | ETH | ALT | MIXED (detected from content)

weekly_digest table → web layout.js → getWeeklyForDate(date)
  → Sidebar "Weekly" section: rotation badge + week range link
  → digest page: WeeklyMacro collapsible banner (above bullet list)
  → RightPanel WeeklyPanel: tabbed view (All/Market/Movers/DeFi/Trending/Stories/Watch)
       Key Stories links → /api/find-digest → matching daily digest date
```

## Snapshot governance proposals

```
── Governance (every 30 min) ────────────────────────────────────────────────
snapshot.fetch_and_store()
  step 1 (cached 24 h): _graphql(_SPACES_QUERY)
       → all verified Snapshot spaces, top 500 by follower count
       → _spaces_cache (module-level, reset on container restart)
  step 2: _graphql(_PROPOSALS_QUERY, {spaces: _spaces_cache})
       → active proposals (state="active"), first 50, ordered by end ASC
       → db.upsert_governance_proposals(rows)  ON CONFLICT(proposal_id) DO UPDATE

governance_proposals table → web layout.js → getGovernanceProposals(limit=6)
  → Sidebar "Governance" section: green dot + space name + title + time remaining
  → links to snapshot.org/#/{space_id}/proposal/{proposal_id}
```

## Social image generation

```
── /api/og (on-demand, Next.js Node.js route) ───────────────────────────────
GET /api/og?date=YYYY-MM-DD&type=daily|weekly|markets|alpha|defi&bullets=N

  → db.getDigest(date)            → digest HTML (Telegram format)
  → parseBullets(content)         → [{title, body}]  (inline parser, mirrors lib/digest.js)
  → detectCat(title + body)       → {label, color}   (10 category regex patterns)
  → Google Fonts v1 API (no UA)   → TTF ArrayBuffer  (Raleway 800, DM Mono 400)
  → readFile(public/falcon.png)   → base64 data URI
  → ImageResponse / satori        → 1080×1080 PNG

Template (full-width Bloomberg-style layout, 1080×1080):
  HEADER  (52px)   [falcon 28px] HORYON · CRYPTO INTELLIGENCE FEED    HORYON.AI
  GOLD BAR (3px)   full-width #D4AF37
  HERO    (90px)   "DAILY EDGE" Raleway 800 78px   |   N SIGNALS · DAY · DATE
                   falcon watermark absolute right, 7% opacity
  HAIRLINE (1px)   rgba(255,255,255,0.08)
  SIGNALS (881px)  flex column, n rows each flex:1
    per row:
      3px left stripe  (category color, 55% opacity)
      number           (DM Mono, category color)
      title            (Raleway 800, 30–44px adaptive, uppercase)
      category pill    (tag label, category color, subtle bg+border)
      description      (single line, 16–18px, muted; hidden at n≥10)
  GOLD RULE (1px)  rgba(212,175,55,0.30)
  FOOTER  (52px)   THE MARKET MOVES. WE HELP YOU SEE IT FIRST.  |  taglines

Adaptive sizing (target readability at ~300px display = 28% scale):
  n ≤ 4   titleSize=44  descMaxLen=110  padV=20
  n ≤ 6   titleSize=40  descMaxLen=92   padV=14  ← default sweet spot
  n ≤ 8   titleSize=36  descMaxLen=76   padV=10
  n = 10  titleSize=32  showDesc=false  padV=8

Caching: fonts (3 parallel fetches) + falcon asset resolved once at first request,
         stored in module-level Promises — all subsequent requests are synchronous reads.

Font loading gotcha: satori/next-og in Next.js 14.x only accepts TTF/OTF.
  Fetching via v1 API with no User-Agent returns format('truetype') CSS → TTF binary.
  Setting a browser-like UA returns woff2 → "Unsupported OpenType signature" crash.
```

## Web layout & sidebar

```
RootLayout (server)
  Promise.all: listDigests + getTvlSnapshot + listWeeklyDigests + getGovernanceProposals
  → <Header>           server; contains <MobileMenuButton> client island
  → <Sidebar>          client; manages data-sidebar (desktop collapse) + data-mobile-nav
      Weekly section   → weeklyItems prop
      Governance       → governance prop (above TVL)
      Archive dates    → items prop
      TVL snapshot     → tvl prop
  → <main.reader>      children (page slot)

Mobile sidebar (≤540px):
  MobileMenuButton sets data-mobile-nav="open" on <html>
  Sidebar (position:fixed, z-index:102) slides in as drawer
  Backdrop: createPortal(<div.sidebar-overlay>, document.querySelector('.shell'))
    position:absolute inset:0 z-index:50   ← within shell stacking context
    z-index 50 < sidebar 102 → clicks pass through to sidebar links
  Auto-close: usePathname() effect sets data-mobile-nav="closed" on navigation
```

## Web digest page

```
DigestPage (server component)
  → parseDigest()        → bullets [{title, body, hack, link}]
  → buildProjectHints()  → projectHints [{protocols, chains, entityTags}]  (parallel SSR)
  → getBulletAnalyses()  → analyses {title → html} (pre-computed, instant)
  → getTvlWithChange()   → tvl strip data
  → getWeeklyForDate()   → weekly macro content + rotation
  → sourceLabel(link)    → {type, name} per bullet
  → <WeeklyMacro>        collapsible banner above bullet feed (server-rendered)
  → <BulletFeed enrichedBullets projectHints analyses tvl market weekly />

BulletFeed (client — owns all state: selectedIndex, panelOpen)
  → <MarketBar />        BTC/ETH price + 24h/7d/30d changes + dominance
  → <TvlStrip />         chain TVL chips with 24h % change
  → <BulletItem selected onSelect />  ×N   (stateless rows)
  → <RightPanel bullet hint cachedAnalysis onClose weekly />

RightPanel (client)
  → cachedAnalysis prop → instant render (no fetch needed when pre-computed)
  → fetch /api/details as fallback (AbortController — cancels on bullet change)
  → ProtocolCard: logo + TVL + ChainDistribution bars (chain_tvls, filters "-borrowed" keys)
  → ChainCard: logo + rank + TVL; word-boundary regex (\bName\b) prevents false matches
  → WeeklyPanel: tabbed sections; movers as chips; Key Stories → /api/find-digest
  → RelatedStories: /api/related → last 30d digests by entity/keyword
```

**Layout rules:**
- `BulletFeed.js` owns all interaction state. Do not add per-bullet state to `BulletItem.js`.
- Sidebar collapse: `Sidebar.js` sets `data-sidebar` on `<html>`; CSS responds. No ShellClient wrapper.
- Mobile drawer: `MobileMenuButton.js` sets `data-mobile-nav` on `<html>`; backdrop portalled into `.shell` (not `document.body`) — `.shell { position:fixed }` creates its own stacking context.
- `sourceLabel()` is computed server-side — plain `{type, name}` object, safe to cross the server→client boundary.
- `cachedAnalysis` prop is the primary path for RightPanel AI content; `/api/details` is only the fallback for rows missing from `digest_bullet_analysis`.

## Database schema

| Table | Key columns | Purpose |
|---|---|---|
| `feed_items` | link (UNIQUE), embedding vector(768) | RSS ingestion store + semantic search |
| `crypto_digest` | date, content, model_used, trigger, error | Daily digest history |
| `crypto_cache` | last_run, last_analysis | 24h freshness cache for digest merge |
| `entity_memory` | slug (PK), type, aliases[], summary, mention_count, twitter_handle, logo_url | Self-building entity knowledge base |
| `analyst_notes` | date, notes, entity_updates JSONB | Post-digest theme extraction |
| `defillama_tvl` | UNIQUE(date, chain) | Chain TVL history |
| `defillama_protocols` | slug (UNIQUE), chain_tvls JSONB | Protocol TVL + metadata |
| `digest_bullet_analysis` | UNIQUE(digest_date, title), analysis | Pre-computed per-bullet AI views |
| `weekly_digest` | UNIQUE(week_start), content, rotation | Weekly macro digest (HTML) |
| `governance_proposals` | proposal_id (UNIQUE), space_id, state, end_ts | Active Snapshot DAO proposals |
| `keyword_analysis` | keyword, chat_id, model_used | Agent query history |
| `chat_history` | chat_id, role, content | Per-chat conversation memory |
| `ingest_run` | started_at, inserted, embedded | Ingest run history |
| `source_health` | url (PK), consecutive_failures | Per-feed health tracking |

## Config tunables (env-overridable)

| Variable | Default | Purpose |
|---|---|---|
| `DIGEST_CHAIN_DAYS` | 3 | Days of past digests injected as chain context |
| `ANALYST_NOTES_DAYS` | 7 | Days of analyst notes injected into prompts |
| `ENTITY_CONTEXT_LIMIT` | 10 | Max entities in pre-digest context block |
| `DIGEST_WINDOW_HOURS` | 24 | Feed window for digest |
| `DIGEST_LIMIT` | 200 | Max feed items per digest |
| `SEARCH_TOPK` | 40 | Agent semantic search results |
| `SEARCH_WINDOW_DAYS` | 30 | Agent search lookback |
| `IVFFLAT_PROBES` | 10 | pgvector recall tuning |
| `AGENT_MAX_STEPS` | 8 | ReAct loop cap |
| `MEMORY_WINDOW` | 20 | Chat history turns loaded |
| `OPENROUTER_MODELS` | see config.py | Comma-separated fallback model list |
| `CMC_API_KEY` | — | CoinMarketCap key; CoinGecko used if absent |
