-- Full current schema. Use this for fresh DB volumes.
-- docker exec -i horyon-db psql -U crypto -d crypto < deploy/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS feed_items (
    id           serial PRIMARY KEY,
    link         text NOT NULL UNIQUE,
    content      text,
    creator      text,
    pub_date     timestamptz,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    source_type  text NOT NULL DEFAULT 'twitter',
    metadata     jsonb NOT NULL DEFAULT '{}',
    embedding    vector(768),
    embed_version smallint NOT NULL DEFAULT 0,
    content_hash text GENERATED ALWAYS AS (md5(content)) STORED,
    mentions     text[] NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS feed_items_content_hash_key ON feed_items (content_hash);

-- ivfflat index: create AFTER initial data load so centroids reflect real data
-- CREATE INDEX feed_items_embedding_idx ON feed_items USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);

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
    embedding       vector(768),  -- embed of the summary, not the raw transcript
    fetched_at      timestamptz,
    summarized_at   timestamptz,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS podcast_episodes_published_idx ON podcast_episodes (published_at DESC);
CREATE INDEX IF NOT EXISTS podcast_episodes_status_idx ON podcast_episodes (status);

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
    centroid       vector(768),
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
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS narratives_state_idx     ON narratives (state);
CREATE INDEX IF NOT EXISTS narratives_momentum_idx  ON narratives (momentum_ratio DESC);

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
