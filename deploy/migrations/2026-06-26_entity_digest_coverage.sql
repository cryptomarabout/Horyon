-- Horyon coverage: how many distinct daily-brief bullets (digest_bullet_analysis)
-- have cited each entity. This is the CURATED-output coverage, distinct from
-- entity_memory.mention_count (raw mentions across all ~107 ingested sources).
-- Populated by app/entity_graph.py on its 6h cron, using the SAME entity matcher as
-- the co-occurrence graph (so it inherits the generic-term blocklist + alias merge).
-- Additive + idempotent — safe to re-run. No web grant change (horyon_web already has
-- SELECT on all tables, which covers new columns).
ALTER TABLE entity_memory
  ADD COLUMN IF NOT EXISTS digest_mention_count int NOT NULL DEFAULT 0;
