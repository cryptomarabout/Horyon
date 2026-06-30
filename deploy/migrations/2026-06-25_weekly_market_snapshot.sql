-- Weekly Market Review redesign — structured market snapshot.
--
-- Adds a JSONB column to weekly_digest holding the point-in-time market levels
-- (BTC/ETH price, ETH/BTC ratio, BTC dominance, total market cap, 24h change)
-- captured when the weekly digest is generated (app/weekly.py:_build_market_snapshot,
-- from the same fetch_market_data already fed to the prompt — no extra API call).
--
-- The Weekly web view reads this column-first for its Market Snapshot block and
-- falls back to parsing the market prose when it is absent (lib/weekly.snapshotCells),
-- so this migration is purely additive and safe to apply at any time. Both
-- app/db/weekly.insert_weekly_digest and web/lib/db/weekly.getWeeklyMarketSnapshots
-- tolerate the column being missing, so order of (deploy bot / apply migration /
-- deploy web) does not matter.
--
-- Apply:  docker exec -i horyon-db psql -U crypto -d crypto < deploy/migrations/2026-06-25_weekly_market_snapshot.sql
-- New weeks populate it automatically; to backfill the current week immediately:
--   docker exec horyon-bot python3 -m app.weekly          # regenerates + persists the snapshot

ALTER TABLE weekly_digest ADD COLUMN IF NOT EXISTS market_snapshot jsonb;
