-- Least-privilege Postgres role for the PUBLIC web container (horyon-web).
--
-- The read site is internet-facing, so it MUST NOT connect as the table owner (`crypto`).
-- This role can read everything the site renders, plus the single write the admin-gated
-- thread composer needs (UPDATE on digest_threads, PATCH /api/thread). Nothing else — no
-- DELETE, no DDL, no access to write paths owned by the bot.
--
-- Apply once on deploy, and again after adding new tables (so the grant covers them).
-- Idempotent — safe to re-run:
--
--   docker exec -i horyon-db \
--     psql -U crypto -d crypto -v web_pw="$WEB_DB_PASSWORD" -f - < deploy/web_db_role.sql
--
-- Then set WEB_DB_PASSWORD in .env and recreate the web container:
--   docker compose up -d web
--
-- NOTE: this is NOT auto-run by the container entrypoint (deploy/schema.sql is, but it
-- can't carry a password). Run it by hand with the command above.

\set ON_ERROR_STOP on

-- Create the login role if it does not exist yet, then (re)set its password every run.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horyon_web') THEN
    CREATE ROLE horyon_web LOGIN;
  END IF;
END
$$;

ALTER ROLE horyon_web WITH PASSWORD :'web_pw';

GRANT CONNECT ON DATABASE crypto TO horyon_web;
GRANT USAGE ON SCHEMA public TO horyon_web;

-- Read everything the public site renders.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO horyon_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO horyon_web;

-- The ONLY write path the web exposes: the admin-gated thread composer edits an
-- existing thread row (it never inserts — bot-created rows, returns 404 otherwise).
GRANT UPDATE ON digest_threads TO horyon_web;
