#!/usr/bin/env bash
# trigger.sh — run a manual bot job inside horyon-bot. Pure name->command
# dispatch. Replaces the old /trigger skill.
#
# Usage:
#   scripts/trigger.sh <job>
#   scripts/trigger.sh            # list jobs
#
# Jobs: tvl protocols entities snapshot weekly weekly-backfill
#       digest digest-dry analyses ingest
# All jobs are idempotent and safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

list_jobs() {
  cat <<'EOF'
Available jobs (scripts/trigger.sh <job>):
  tvl              Fetch + store chain TVL snapshot
  protocols        Fetch + store protocol TVL (every 2h in prod)
  entities         Seed entity_memory from defillama_protocols
  snapshot         Fetch active Snapshot DAO governance proposals
  weekly           Regenerate current week's macro digest
  weekly-backfill  Fill all missing weekly digests (historical, no live market data)
  digest           Run today's digest (persists + triggers bullet analyses)
  digest-dry       Build today's digest without writing (prints output)
  analyses         Regenerate all digest_bullet_analysis rows
  ingest           Run one ingest cycle (fetch + embed + entity extraction)
EOF
}

JOB="${1:-}"
case "$JOB" in
  dry) JOB=digest-dry ;;
  gov) JOB=snapshot ;;
esac

case "$JOB" in
  tvl)             CMD='from app.defillama import fetch_and_store; print(fetch_and_store(), "rows")'; PY=-c ;;
  protocols)       CMD='from app.defillama import fetch_and_store_protocols; print(fetch_and_store_protocols(), "rows")'; PY=-c ;;
  entities)        CMD='from app.db import seed_entities_from_protocols; print(seed_entities_from_protocols(), "seeded")'; PY=-c ;;
  snapshot)        MOD=app.snapshot ;;
  weekly)          MOD=app.weekly ;;
  weekly-backfill) MOD=app.weekly; ARG=--backfill ;;
  digest)          MOD=app.digest ;;
  digest-dry)      MOD=app.digest; ARG=--no-persist ;;
  analyses)        MOD=app.digest; ARG=--regen-analyses ;;
  ingest)          MOD=app.ingest ;;
  ""|help|-h|--help) list_jobs; exit 0 ;;
  *) echo "trigger.sh: unknown job '$JOB'." >&2; echo >&2; list_jobs >&2; exit 2 ;;
esac

if [ "$(docker inspect horyon-bot --format '{{.State.Status}}' 2>/dev/null || echo missing)" != "running" ]; then
  echo "trigger.sh: horyon-bot is not running." >&2
  exit 1
fi

if [ "${PY:-}" = "-c" ]; then
  echo ">> python3 -c '$CMD'"
  docker exec horyon-bot python3 -c "$CMD"
else
  echo ">> python3 -m $MOD ${ARG:-}"
  docker exec horyon-bot python3 -m "$MOD" ${ARG:-}
fi
