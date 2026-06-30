-- 2026-06-25 · Research redesign of /narratives
-- Three additive, nullable columns on `narratives`. Safe to run online: no table
-- rewrite (Postgres 11+ stores the key_points default in the catalog), idempotent
-- (IF NOT EXISTS). horyon_web and horyon_readonly already hold table-level SELECT
-- (GRANT SELECT ON ALL TABLES IN SCHEMA public) which automatically covers new
-- columns — no re-grant required.
--
-- Apply on the live DB:
--   docker exec -i horyon-db psql -U crypto -d crypto < deploy/migrations/2026-06-25_research_narratives.sql
--
-- Then backfill. `sector` + `source_count` are deterministic and `key_points` is
-- synthesised on the next rebuild:
--   docker exec horyon-bot python3 -m app.narratives

ALTER TABLE narratives ADD COLUMN IF NOT EXISTS sector       text;   -- primary research sector (deterministic classifier, app/narratives.py:_sector)
ALTER TABLE narratives ADD COLUMN IF NOT EXISTS source_count int;    -- distinct source domains across member signals (honest breadth, not inflated)
ALTER TABLE narratives ADD COLUMN IF NOT EXISTS key_points   text[] NOT NULL DEFAULT '{}';  -- 2-3 executive takeaways for the brief abstract

-- Sector filtering / future server-side sector queries.
CREATE INDEX IF NOT EXISTS narratives_sector_idx ON narratives (sector);
