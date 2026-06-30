-- Pure read-only Postgres role for test machines running against the prod DB.
--
-- This role has ONLY SELECT on all tables — no INSERT, UPDATE, DELETE, DDL.
-- Any write attempt from a container using this role fails with a Postgres
-- permission error, regardless of what the application code tries to do.
--
-- Apply on prod once (and again after adding new tables so the grant covers them):
--
--   READONLY_PW=$(grep READONLY_DB_PASSWORD .env | cut -d= -f2-)
--   docker exec -i horyon-db \
--     psql -U crypto -d crypto -v "readonly_pw=$READONLY_PW" -f - \
--     < deploy/readonly_db_role.sql
--
-- Then set READONLY_DB_PASSWORD in prod .env (so you can share it with test machines):
--   echo "READONLY_DB_PASSWORD=<password>" >> .env
--
-- The test machine uses this credential via docker-compose.external-db.yml.
-- No further setup needed on the test machine.

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'horyon_readonly') THEN
    CREATE ROLE horyon_readonly LOGIN;
  END IF;
END
$$;

ALTER ROLE horyon_readonly WITH PASSWORD :'readonly_pw';

GRANT CONNECT ON DATABASE crypto TO horyon_readonly;
GRANT USAGE ON SCHEMA public TO horyon_readonly;

-- Read everything — nothing else.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO horyon_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO horyon_readonly;
