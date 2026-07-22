-- Full current schema. Use this for fresh DB volumes.
-- docker exec -i horyon-db psql -U crypto -d crypto < deploy/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
-- Trigram index support for the public site's word-boundary regex matches on feed content
-- (entity-tag clicks + bullet source corroboration use `content ~* '\y…\y'`, which can't use
-- the FTS GIN index and would otherwise sequential-scan feed_items on every request).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS feed_items (
    id           serial PRIMARY KEY,
    link         text NOT NULL UNIQUE,
    content      text,
    creator      text,
    pub_date     timestamptz,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    source_type  text NOT NULL DEFAULT 'twitter',
    metadata     jsonb NOT NULL DEFAULT '{}',
    embedding    vector(2048),   -- nvidia/nemotron-3-embed-1b (NIM); was 768 (Ollama nomic) pre-2026-07-21
    embed_version smallint NOT NULL DEFAULT 0,
    content_hash text GENERATED ALWAYS AS (md5(content)) STORED,
    mentions     text[] NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS feed_items_content_hash_key ON feed_items (content_hash);

-- Ingest-time quality gate (title_content_coherence_check in app/ingest.py): the raw RSS
-- item title + a cheap heuristic flag ('ok' | 'thin_content' | 'nitter_handle_title' |
-- 'boilerplate_title'). Consumed by scoring corroboration (thin items don't corroborate)
-- and the LLM input paths (search_feed excludes thin items; the digest prompt annotates
-- thin/roundup items). Already live in prod; declared here so fresh volumes match.
ALTER TABLE feed_items ADD COLUMN IF NOT EXISTS title varchar(512);
ALTER TABLE feed_items ADD COLUMN IF NOT EXISTS quality_flag varchar(32) NOT NULL DEFAULT 'ok';

-- Full-text search column for the PUBLIC free-text search bar. HTML is stripped first
-- (regexp_replace is IMMUTABLE, so it is allowed in a generated column) so tags don't
-- pollute the lexeme set. This replaces per-query Ollama embedding on the free-text
-- path: pure in-DB FTS scales to high QPS with no external call (see web/api/search).
ALTER TABLE feed_items ADD COLUMN IF NOT EXISTS content_tsv tsvector
    GENERATED ALWAYS AS (
        to_tsvector('english', regexp_replace(coalesce(content, ''), '<[^>]+>', ' ', 'g'))
    ) STORED;
CREATE INDEX IF NOT EXISTS feed_items_content_tsv_idx ON feed_items USING GIN (content_tsv);

-- Accelerates the public site's `content ~* '\y<term>\y'` regex queries (entity feed +
-- bullet source corroboration) — pg_trgm GIN serves case-insensitive regex/ILIKE matches.
CREATE INDEX IF NOT EXISTS feed_items_content_trgm_idx ON feed_items USING GIN (content gin_trgm_ops);
-- Supports the `COALESCE(pub_date, ingested_at) >= now() - INTERVAL '30 days'` window prune
-- those same queries apply (and the FTS recency ordering), so the date filter uses an index
-- instead of scanning every row.
CREATE INDEX IF NOT EXISTS feed_items_effective_date_idx
    ON feed_items (COALESCE(pub_date, ingested_at) DESC);

-- NO approximate vector index: nvidia/nemotron-3-embed-1b emits 2048-dim vectors and pgvector's
-- ivfflat/hnsw indexes cap at 2000 dims, so search_feed does an exact cosine scan (~tens of ms at
-- this corpus size). `SET LOCAL ivfflat.probes` in search_feed is a harmless no-op without an index.
-- At much larger scale, switch the column to halfvec(2048) + an hnsw index (indexable to 4000 dims).
-- (A vector(768) ivfflat index existed pre-2026-07-21; the migration below drops it.)

CREATE TABLE IF NOT EXISTS crypto_cache (
    id            serial PRIMARY KEY,
    last_run      timestamptz,
    last_analysis text
);

CREATE TABLE IF NOT EXISTS crypto_digest (
    created_at  timestamptz NOT NULL DEFAULT now() PRIMARY KEY,
    date        date        NOT NULL,
    content     text,
    model_used  text        NOT NULL DEFAULT '',
    trigger     text        NOT NULL DEFAULT 'manual',
    duration_ms int,
    error       text
);

CREATE TABLE IF NOT EXISTS keyword_analysis (
    id          bigserial   PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    keyword     text        NOT NULL,
    chat_id     text        NOT NULL DEFAULT '',
    model_used  text        NOT NULL DEFAULT '',
    duration_ms int
);
CREATE INDEX IF NOT EXISTS keyword_analysis_created_idx ON keyword_analysis (created_at DESC);

CREATE TABLE IF NOT EXISTS chat_history (
    id         bigserial PRIMARY KEY,
    chat_id    text NOT NULL,
    role       text NOT NULL CHECK (role IN ('user', 'assistant')),
    content    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_history_chat_idx ON chat_history (chat_id, id);

CREATE TABLE IF NOT EXISTS ingest_run (
    id             serial PRIMARY KEY,
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz NOT NULL DEFAULT now(),
    raw            int,
    cleaned        int,
    inserted       int,
    embedded       int,
    sources_ok     int,
    sources_failed int,
    duration_ms    int
);
CREATE INDEX IF NOT EXISTS ingest_run_started_idx ON ingest_run (started_at DESC);

CREATE TABLE IF NOT EXISTS source_health (
    url                  text PRIMARY KEY,
    last_ok              boolean,
    last_status          int,
    last_item_count      int,
    last_error           text,
    consecutive_failures int NOT NULL DEFAULT 0,
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS defillama_tvl (
    id         serial PRIMARY KEY,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    date       date        NOT NULL,
    chain      text        NOT NULL,
    tvl_usd    double precision NOT NULL,
    UNIQUE (date, chain)
);
CREATE INDEX IF NOT EXISTS defillama_tvl_date_idx ON defillama_tvl (date DESC);

CREATE TABLE IF NOT EXISTS defillama_protocols (
    id            serial PRIMARY KEY,
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    slug          text UNIQUE NOT NULL,
    name          text,
    category      text,
    chains        text[],
    chain_tvls    jsonb DEFAULT '{}',
    tvl_usd       double precision,
    tvl_change_1d double precision,
    tvl_change_7d double precision,
    mcap_tvl      double precision,
    token_symbol  text,
    logo_url      text,
    url           text,
    description   text,
    gecko_id      text
);

-- CoinGecko market-data snapshot (price/mcap/FDV/supply) — analyst-grade token facts,
-- refreshed on a cron (app.defillama.fetch_and_store_market_data). Joined to entities by
-- gecko_id == entity_memory.slug (see docs/conventions.md for why that's a reliable key).
CREATE TABLE IF NOT EXISTS coingecko_market (
    gecko_id             text        PRIMARY KEY,
    symbol               text,
    price_usd            double precision,
    market_cap_usd       double precision,
    fdv_usd              double precision,
    circulating_supply   double precision,
    total_supply         double precision,
    market_cap_rank      int,
    price_change_24h_pct double precision,
    price_change_7d_pct  double precision,
    fetched_at           timestamptz NOT NULL DEFAULT now()
);

-- Entity memory: one row per tracked entity (protocol, chain, fund, person, exchange).
-- Self-populates from ingest-time LLM extraction; warm-start with seed_entities_from_protocols().
CREATE TABLE IF NOT EXISTS entity_memory (
    slug           text PRIMARY KEY,
    name           text NOT NULL,
    type           text NOT NULL CHECK (type IN ('protocol', 'chain', 'fund', 'person', 'exchange', 'dao', 'other')),
    aliases        text[]      NOT NULL DEFAULT '{}',
    summary        text,
    last_mentioned date,
    mention_count  int         NOT NULL DEFAULT 1,
    digest_mention_count int   NOT NULL DEFAULT 0,  -- "Horyon coverage": distinct daily-brief bullets citing this entity (app/entity_graph.py)
    updated_at     timestamptz NOT NULL DEFAULT now(),
    twitter_handle text,
    logo_url       text
);
CREATE INDEX IF NOT EXISTS entity_memory_type_idx ON entity_memory (type);
CREATE INDEX IF NOT EXISTS entity_memory_last_mentioned_idx ON entity_memory (last_mentioned DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS entity_memory_aliases_gin ON entity_memory USING GIN (aliases);

-- Weekly macro digest: market rotation, top movers, DeFi pulse, trending dapps.
CREATE TABLE IF NOT EXISTS weekly_digest (
    id          serial      PRIMARY KEY,
    week_start  date        NOT NULL,
    week_end    date        NOT NULL,
    content     text,
    rotation    text        NOT NULL DEFAULT 'MIXED', -- BTC | ETH | ALT | MIXED
    model_used  text        NOT NULL DEFAULT '',
    trigger     text        NOT NULL DEFAULT 'cron',
    duration_ms int,
    error       text,
    market_snapshot jsonb,   -- structured market levels for the Weekly Market Snapshot block
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (week_start)
);
CREATE INDEX IF NOT EXISTS weekly_digest_week_start_idx ON weekly_digest (week_start DESC);

-- Pre-computed analyst view per digest bullet (generated post-digest, served instantly on web).
CREATE TABLE IF NOT EXISTS digest_bullet_analysis (
    id          serial      PRIMARY KEY,
    digest_date date        NOT NULL,
    title       text        NOT NULL,
    body        text        NOT NULL DEFAULT '',
    analysis    text        NOT NULL,
    model_used  text        NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    importance_score smallint,          -- 0–100 composite signal score (NULL if scoring failed)
    source_count     smallint,          -- distinct corroborating source domains in the 24h window
    score_breakdown  jsonb,             -- per-signal detail: {"s1":..,"llm_adjustment":..,"position_bonus":..,"decay":..}
    UNIQUE (digest_date, title)
);
CREATE INDEX IF NOT EXISTS digest_bullet_analysis_date_idx ON digest_bullet_analysis (digest_date DESC);

-- Twitter/X thread rendering of a daily digest (one row per digest date).
-- Built post-digest by app/threads.py from digest_bullet_analysis (already grounded +
-- importance-scored). The hook tweet carries the /api/og card; one tweet per bullet,
-- ordered by importance to match the card. `status` is the contract with the external poster.
CREATE TABLE IF NOT EXISTS digest_threads (
    digest_date  date        PRIMARY KEY,
    hook         text        NOT NULL,                    -- tweet 1 (OG image attaches here)
    tweets       jsonb       NOT NULL,                    -- [{title, text, link, importance_score}] in post order
    cta          text        NOT NULL DEFAULT '',         -- optional closing tweet
    og_image_url text        NOT NULL DEFAULT '',         -- /api/og card to attach to the hook
    model_used   text        NOT NULL DEFAULT '',
    status       text        NOT NULL DEFAULT 'pending',  -- 'pending' | 'posted' | 'blocked' (fail-closed safety gate; web requires explicit unblock before posting)
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Daily audio briefing: a spoken digest rendered in THREE length variants per date
-- ('short' ~90-sec flash, 'standard' ~6-min two-voice podcast, 'explainer' ~12-min deep dive).
-- Built post-digest by app/briefing.py — an LLM rewrites the day's top bullet analyses
-- (grounded, no new facts) into a spoken script, which a free TTS engine (app/tts.py)
-- renders to audio stored inline as bytea (~2 MB/day/variant). Served on the web via a Range-aware
-- /api/audio/[date]?variant= route and sent to Telegram alongside the digest. `status` carries the
-- lifecycle so a blocked/failed render is never delivered. PK is (digest_date, variant).
CREATE TABLE IF NOT EXISTS digest_audio (
    digest_date  date        NOT NULL,
    variant      text        NOT NULL DEFAULT 'standard',  -- 'short' | 'standard' | 'explainer' (see app/briefing.py)
    script       text        NOT NULL,                    -- the spoken script (re-synth, captions, debug)
    audio        bytea,                                   -- rendered audio bytes (NULL until synth succeeds)
    mime         text        NOT NULL DEFAULT 'audio/mpeg',
    voice        text        NOT NULL DEFAULT '',          -- e.g. 'en-US-AriaNeural'
    tts_engine   text        NOT NULL DEFAULT '',          -- 'edge' | 'piper'
    duration_sec smallint,                                -- for the Telegram player + UI label
    byte_size    int,
    word_count   smallint,
    model_used   text        NOT NULL DEFAULT '',          -- the script LLM
    status       text        NOT NULL DEFAULT 'pending',   -- pending | ready | blocked | failed
    chapters     jsonb,                                    -- [{title, start}] segment markers for in-player nav
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (digest_date, variant)
);
-- Additive for DBs created before chapters existed (idempotent).
ALTER TABLE digest_audio ADD COLUMN IF NOT EXISTS chapters jsonb;
-- Additive: pseudo-waveform array (100 floats 0-1) for the web player bar visualisation.
ALTER TABLE digest_audio ADD COLUMN IF NOT EXISTS waveform jsonb;
-- Additive: three length variants per date. Older DBs had digest_date as the sole PK; add the
-- column (existing rows default to 'standard') and repoint the PK to (digest_date, variant).
-- Idempotent — the DO block is a no-op once 'variant' is part of the primary key.
ALTER TABLE digest_audio ADD COLUMN IF NOT EXISTS variant text NOT NULL DEFAULT 'standard';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
        WHERE i.indrelid = 'digest_audio'::regclass AND i.indisprimary AND a.attname = 'variant'
    ) THEN
        ALTER TABLE digest_audio DROP CONSTRAINT IF EXISTS digest_audio_pkey;
        ALTER TABLE digest_audio ADD PRIMARY KEY (digest_date, variant);
    END IF;
END $$;

-- Pre-rendered /api/og social card per digest date. The bot renders it once post-digest
-- (via the web route over the internal docker network) and stores the PNG here; the public
-- /api/og route then serves these bytes instead of rendering on every request — removing a
-- per-request compute-DoS vector on the public site. See app/og_cache.py.
CREATE TABLE IF NOT EXISTS digest_og (
    digest_date  date        PRIMARY KEY,
    png          bytea       NOT NULL,                   -- rendered 1200x628 card
    mime         text        NOT NULL DEFAULT 'image/png',
    byte_size    int,
    source_at    timestamptz,                            -- the digest created_at this card was rendered from
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Mirrored entity avatars for the Entity Map. The map used to resolve every node's
-- avatar in the BROWSER (logo_url, else twitter_handle → unavatar.io) in one burst on
-- load, which unavatar rate-limited → many nodes silently fell back to a monogram. The
-- bot now fetches each map entity's avatar once (server-side — the only place allowed to
-- make external calls), stores the bytes here, and the public /api/avatar/[slug] route
-- serves them from our own DB. No per-request external call, no client stampede. The
-- original logo_url/unavatar URLs remain a client-side fallback. See app/avatars.py.
CREATE TABLE IF NOT EXISTS entity_avatars (
    slug         text        PRIMARY KEY REFERENCES entity_memory(slug) ON DELETE CASCADE,
    image        bytea       NOT NULL,                   -- the avatar bytes (jpeg/png/webp)
    mime         text        NOT NULL DEFAULT 'image/png',
    source_url   text        NOT NULL,                   -- the URL the bytes were fetched from
    etag         text,                                   -- upstream ETag, when present (debug)
    byte_size    int,
    fetched_at   timestamptz NOT NULL DEFAULT now()
);

-- Snapshot DAO governance proposals (active + recently closed).
CREATE TABLE IF NOT EXISTS governance_proposals (
    id           serial       PRIMARY KEY,
    proposal_id  text         NOT NULL UNIQUE,   -- Snapshot hash (0x...)
    space_id     text         NOT NULL,           -- e.g. 'aave.eth'
    space_name   text         NOT NULL,           -- e.g. 'Aave'
    title        text         NOT NULL,
    state        text         NOT NULL,           -- 'active' | 'pending' | 'closed'
    start_ts     timestamptz,
    end_ts       timestamptz,
    fetched_at   timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS governance_proposals_state_end_idx ON governance_proposals (state, end_ts);

-- Pre-computed entity intel briefs: updated post-digest for each entity mentioned in that day's bullets.
CREATE TABLE IF NOT EXISTS entity_intel_brief (
    entity_name  TEXT        PRIMARY KEY,
    brief_html   TEXT        NOT NULL,
    model_used   TEXT        NOT NULL DEFAULT '',
    digest_date  DATE        NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS entity_intel_brief_date_idx ON entity_intel_brief (digest_date DESC);

-- Analyst notes: extracted themes + entity state updates after each digest run.
CREATE TABLE IF NOT EXISTS analyst_notes (
    id             serial      PRIMARY KEY,
    date           date        NOT NULL,
    notes          text        NOT NULL,
    entity_updates jsonb       NOT NULL DEFAULT '{}',
    source         text        NOT NULL DEFAULT 'digest',
    model_used     text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS analyst_notes_date_idx ON analyst_notes (date DESC);

-- YouTube crypto-podcast episodes: transcript + LLM map-reduce analysis.
-- Raw transcript kept separate from feed_items (too large); only the distilled
-- summary is embedded and fed into the daily digest.
CREATE TABLE IF NOT EXISTS podcast_episodes (
    id              serial      PRIMARY KEY,
    video_id        text        NOT NULL UNIQUE,
    channel         text        NOT NULL,
    channel_id      text,
    title           text,
    url             text,
    published_at    timestamptz,
    duration_sec    int,
    transcript      text,
    transcript_lang text,
    status          text        NOT NULL DEFAULT 'pending',  -- pending|summarized|failed|skipped
    summary         text,
    analysis        jsonb,        -- {tldr, themes, notable_claims, predictions, entities, guests, sentiment}
    model_used      text        NOT NULL DEFAULT '',
    embedding       vector(2048),  -- embed of the summary, not the raw transcript (2048: NIM nemotron-3-embed-1b)
    fetched_at      timestamptz,
    summarized_at   timestamptz,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS podcast_episodes_published_idx ON podcast_episodes (published_at DESC);
CREATE INDEX IF NOT EXISTS podcast_episodes_status_idx ON podcast_episodes (status);

-- Podcast prediction follow-through (T14). Each 'prediction' the reduce pass extracts
-- becomes a row here at summarize time; a monthly DETERMINISTIC recheck
-- (podcasts.recheck_predictions — zero LLM) word-boundary-matches each open prediction's
-- entities against later digest coverage and records whether related coverage has since
-- appeared. This is the only place the system tracks forward-looking claims. outcome:
-- 'open' (awaiting) | 'corroborated' (matching later coverage found) | 'stale' (window
-- elapsed, no coverage). It is a HONEST "coverage appeared" signal, never an LLM verdict
-- on whether the call was right.
CREATE TABLE IF NOT EXISTS podcast_predictions (
    id           serial      PRIMARY KEY,
    video_id     text        NOT NULL,
    channel      text        NOT NULL DEFAULT '',
    claim        text        NOT NULL,
    entities     jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- resolved entity slugs the claim names
    predicted_at timestamptz,                               -- the episode's published_at
    outcome      text        NOT NULL DEFAULT 'open',       -- open | corroborated | stale
    evidence     jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- [{date, title, link}] matching digest bullets
    checked_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (video_id, claim)
);
CREATE INDEX IF NOT EXISTS podcast_predictions_outcome_idx ON podcast_predictions (outcome);
CREATE INDEX IF NOT EXISTS podcast_predictions_predicted_idx ON podcast_predictions (predicted_at DESC);

-- Narratives: persistent clusters of cross-source signals carrying a momentum state.
-- Built periodically (post-digest + cron) by app/narratives.py from digest_bullet_analysis
-- (news), podcast_episodes (podcast), and governance_proposals (governance). The momentum
-- model (R/B/rho/state) mirrors app/scoring.py: mass = importance/100, windows anchored to a
-- reference time. Full-rebuild semantics — clearing + repopulating both tables is safe.
CREATE TABLE IF NOT EXISTS narratives (
    slug           text        PRIMARY KEY,
    label          text        NOT NULL,
    thesis         text,
    watch_next     text[]      NOT NULL DEFAULT '{}',        -- AI "what to watch next" items
    contrarian     text,                                     -- one dissenting/counter signal, if any
    entity_slugs   text[]      NOT NULL DEFAULT '{}',
    centroid       vector(2048),   -- 2048: NIM nemotron-3-embed-1b (was 768 Ollama nomic); rebuilt fresh each cron

    state          text        NOT NULL DEFAULT 'forming',  -- forming|heating|steady|cooling|dormant
    intensity_48h  real,                                    -- R: weighted signal mass, last 48h
    baseline       real,                                    -- B: prior-5d 48h-equivalent mass
    momentum_ratio real,                                    -- rho = (R+1)/(B+1)
    delta_48h      int         NOT NULL DEFAULT 0,           -- raw signal count, last 48h (display badge)
    signal_count   int         NOT NULL DEFAULT 0,          -- total member signals
    dominant_type  text,                                    -- news|podcast|governance|market
    severity       text,                                    -- red|gold|green|neutral (dominant signals)
    first_seen     date,
    last_signal_at timestamptz,
    model_used     text        NOT NULL DEFAULT '',
    sector         text,                                    -- primary research sector (deterministic classify, app/narratives.py:_sector)
    source_count   int,                                     -- distinct source domains across member signals (honest breadth)
    key_points     text[]      NOT NULL DEFAULT '{}',        -- 2-3 executive takeaways for the research brief abstract
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS narratives_state_idx     ON narratives (state);
CREATE INDEX IF NOT EXISTS narratives_momentum_idx  ON narratives (momentum_ratio DESC);
CREATE INDEX IF NOT EXISTS narratives_sector_idx    ON narratives (sector);

-- Join: which signals belong to a narrative. Denormalized (title/url/importance/ts) so the
-- web evidence timeline renders without joining back to three source tables.
CREATE TABLE IF NOT EXISTS narrative_signals (
    narrative_slug text        NOT NULL REFERENCES narratives(slug) ON DELETE CASCADE,
    signal_type    text        NOT NULL,                    -- news|podcast|governance|market
    signal_ref     text        NOT NULL,                    -- bullet fingerprint | video_id | proposal_id
    title          text,
    body           text,
    url            text,
    importance     smallint,
    ts             timestamptz,
    PRIMARY KEY (narrative_slug, signal_type, signal_ref)
);
CREATE INDEX IF NOT EXISTS narrative_signals_slug_idx ON narrative_signals (narrative_slug);
CREATE INDEX IF NOT EXISTS narrative_signals_ts_idx   ON narrative_signals (ts DESC);

-- Entity co-occurrence graph: two entities linked when mentioned in the same feed
-- item. Full-rebuild precompute (app/entity_graph.py, cron) over the last N days.
-- slug_a < slug_b (undirected, deduped). weight = #items the pair co-occurred in.
CREATE TABLE IF NOT EXISTS entity_edges (
    slug_a    text        NOT NULL,
    slug_b    text        NOT NULL,
    weight    integer     NOT NULL DEFAULT 0,  -- raw co-mention count
    npmi      real,                            -- association strength ∈ [-1,1] (down-weights hubs)
    examples  jsonb       NOT NULL DEFAULT '[]'::jsonb,  -- ≤3 {link, ts, snippet} evidence rows
    last_seen timestamptz,
    PRIMARY KEY (slug_a, slug_b)
);
CREATE INDEX IF NOT EXISTS entity_edges_a_idx      ON entity_edges (slug_a);
CREATE INDEX IF NOT EXISTS entity_edges_b_idx      ON entity_edges (slug_b);
CREATE INDEX IF NOT EXISTS entity_edges_weight_idx ON entity_edges (weight DESC);

-- Entity review verdicts — durable decision memory for entity hygiene (2026-07-05).
-- 'block': the slug is confirmed extraction junk (e.g. an entity literally named
--   "would"/"zero") — upsert_entity refuses to (re)create it, killing the
--   audit → delete → re-extracted-next-cycle loop.
-- 'keep': a human reviewed a flagged collision-risk entity and confirmed it is a
--   real project — the stored-data audit stops re-flagging it on every run.
-- Written by `python3 -m app.entity_audit purge-collisions` / `keep`, and read by
-- app/db/entities.upsert_entity + app/audit.check_collision_risk.
CREATE TABLE IF NOT EXISTS entity_review (
    slug       text PRIMARY KEY,
    verdict    text NOT NULL CHECK (verdict IN ('keep', 'block')),
    reason     text NOT NULL DEFAULT '',
    decided_at timestamptz NOT NULL DEFAULT now()
);

-- Scored LLM grounding eval runs (T10/T11 — app/eval_harness.py, weekly cron + on-demand
-- before prompt/model-chain changes). One row per path×case; rows in the same run share
-- run_at. checks = {check_name: bool}. Bot-only: the web never reads this table, so no
-- web_db_role.sql grant is needed.
CREATE TABLE IF NOT EXISTS eval_runs (
    id         serial      PRIMARY KEY,
    run_at     timestamptz NOT NULL DEFAULT now(),
    run_kind   text        NOT NULL DEFAULT 'manual',  -- 'manual' | 'weekly-cron'
    path       text        NOT NULL,  -- digest | analyst | brief | thread | briefing | narrative
    case_name  text        NOT NULL,  -- 'baseline' | 'inj-*' (prompt-injection suite)
    model_used text        NOT NULL DEFAULT '',
    checks     jsonb       NOT NULL,
    passed     int         NOT NULL,
    failed     int         NOT NULL,
    notes      text        NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS eval_runs_run_at_idx ON eval_runs (run_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2026-07-21: embedding provider migration — host Ollama nomic-embed-text (768-dim) →
-- NVIDIA NIM nvidia/nemotron-3-embed-1b (2048-dim). The two models' vectors are not
-- comparable AND a 768-dim vector cannot be cast to a 2048 column, so existing embeddings
-- are NULLed then re-embedded. feed_items re-embeds via the ingest self-heal / a one-shot
-- `docker exec horyon-bot python3 -m app.reembed`; podcast_episodes.embedding and
-- narratives.centroid are write-only (never read for similarity) so they simply repopulate at
-- 2048 on the next summarize / narratives rebuild. The pre-existing vector(768) ivfflat index is
-- dropped: pgvector ivfflat/hnsw cap at 2000 dims, so 2048 is served by an exact scan.
-- Guarded on the live column dimension — the whole block is a no-op once already vector(2048).
DO $$
BEGIN
    IF (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
         WHERE a.attrelid = 'feed_items'::regclass AND a.attname = 'embedding') = 'vector(768)' THEN
        DROP INDEX IF EXISTS feed_items_embedding_idx;
        UPDATE feed_items SET embedding = NULL WHERE embedding IS NOT NULL;
        ALTER TABLE feed_items ALTER COLUMN embedding TYPE vector(2048);
        UPDATE feed_items SET embed_version = 0;   -- force full re-embed at the new EMBED_VERSION
        RAISE NOTICE 'feed_items.embedding migrated 768 -> 2048 (% rows pending re-embed)',
                     (SELECT count(*) FROM feed_items);
    END IF;

    IF (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
         WHERE a.attrelid = 'podcast_episodes'::regclass AND a.attname = 'embedding') = 'vector(768)' THEN
        UPDATE podcast_episodes SET embedding = NULL WHERE embedding IS NOT NULL;
        ALTER TABLE podcast_episodes ALTER COLUMN embedding TYPE vector(2048);
        RAISE NOTICE 'podcast_episodes.embedding migrated 768 -> 2048';
    END IF;

    IF (SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
         WHERE a.attrelid = 'narratives'::regclass AND a.attname = 'centroid') = 'vector(768)' THEN
        UPDATE narratives SET centroid = NULL WHERE centroid IS NOT NULL;
        ALTER TABLE narratives ALTER COLUMN centroid TYPE vector(2048);
        RAISE NOTICE 'narratives.centroid migrated 768 -> 2048';
    END IF;
END $$;

-- Intraday digest updates (app/intraday.py — 13:00 + 19:00 UTC crons). The 07:00 daily
-- digest (crypto_digest) is the canonical morning brief; these are lightweight INCREMENTAL
-- follow-ups covering only feed items since the last digest/update that day, sent to Telegram
-- and shown on the web Daily Briefs page as an "Intraday updates" timeline under the brief.
-- One row per (date, seq); seq is 1-based within the day. `content` is the same cleaned
-- Telegram-HTML bullet block as crypto_digest.content (parsed by the same parseDigest).
-- Web READS this table → deploy/web_db_role.sql must be re-run after adding it.
CREATE TABLE IF NOT EXISTS digest_updates (
    created_at   timestamptz NOT NULL DEFAULT now() PRIMARY KEY,
    date         date        NOT NULL,
    seq          int         NOT NULL DEFAULT 1,      -- 1-based ordinal within the day
    content      text,                                -- cleaned Telegram-HTML bullet block
    model_used   text        NOT NULL DEFAULT '',
    window_start timestamptz,                         -- items since this ts were summarized
    item_count   int,                                 -- new feed items considered
    trigger      text        NOT NULL DEFAULT 'cron', -- 'cron' | 'manual'
    error        text
);
CREATE INDEX IF NOT EXISTS digest_updates_date_idx ON digest_updates (date DESC, created_at DESC);

-- Breaking-news alerts already pushed to Telegram (app/alerts.py, off the 20-min ingest
-- cycle). Deterministic — NO LLM: an alert quotes a feed item's own title + source + link.
-- One row per alerted feed-item link; drives dedup (never re-alert the same story as more
-- outlets pick it up) + the per-hour/per-day rate cap. Bot-only (alerts are Telegram-only,
-- not on the web) → no web_db_role.sql grant needed.
CREATE TABLE IF NOT EXISTS alerts_sent (
    link       text        PRIMARY KEY,
    title      text        NOT NULL DEFAULT '',
    score      smallint,                         -- 0–100 importance at send time
    category   text        NOT NULL DEFAULT '',  -- 'security' | 'funding' | 'launch' | 'governance' | ''
    source_key text        NOT NULL DEFAULT '',  -- scoring.get_source_key(link)
    sent_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alerts_sent_sent_at_idx ON alerts_sent (sent_at DESC);
