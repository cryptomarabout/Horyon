#!/usr/bin/env bash
# status.sh — full health snapshot of the Horyon stack in one pass. Pre-formatted
# so a human can read it directly with zero Claude tokens. Replaces /status.
#
# Usage: scripts/status.sh
set -uo pipefail
cd "$(dirname "$0")/.."

PSQL() { docker exec horyon-db psql -U crypto -d crypto -tA -F ' | ' -c "$1" 2>&1; }
DOMAIN="${PUBLIC_URL:-http://localhost/}"   # set PUBLIC_URL to your deployed domain

echo "=== CONTAINERS ==="
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'horyon-' | sort || echo "docker ps failed"

echo
echo "=== BOT ERRORS (last 150 lines) ==="
ERRS=$(docker compose logs --tail=150 bot 2>&1 | grep -E '(ERROR|Traceback|Exception|CRITICAL)' | tail -15)
[ -n "$ERRS" ] && echo "$ERRS" || echo "none"

echo
echo "=== INGEST (last 5 runs) ==="
PSQL "SELECT started_at, inserted, embedded FROM ingest_run ORDER BY started_at DESC LIMIT 5;"
echo "-- last item / total:"
PSQL "SELECT MAX(ingested_at), COUNT(*) FROM feed_items;"

echo
echo "=== DIGESTS (last 5) ==="
PSQL "SELECT date, length(content) AS len, model_used, trigger, (error IS NOT NULL) AS err FROM crypto_digest ORDER BY date DESC LIMIT 5;"

echo
echo "=== INTEL LAYER ==="
PSQL "SELECT
  (SELECT COUNT(*) FROM entity_memory) AS entities,
  (SELECT COUNT(*) FROM analyst_notes) AS notes,
  (SELECT COUNT(*) FROM digest_bullet_analysis) AS analyses,
  (SELECT COUNT(*) FROM governance_proposals WHERE state='active') AS active_props,
  (SELECT COUNT(*) FROM weekly_digest) AS weekly;"

echo
echo "=== FEED HEALTH (>2 consecutive failures) ==="
FAILS=$(PSQL "SELECT url, consecutive_failures FROM source_health WHERE consecutive_failures > 2 ORDER BY consecutive_failures DESC LIMIT 10;")
[ -n "$FAILS" ] && echo "$FAILS" || echo "all sources healthy"

echo
echo "=== WEB (via Caddy) ==="
CODE=$(curl -sk "$DOMAIN" -o /dev/null -w '%{http_code}' --max-time 10 || echo 'unreachable')
case "$CODE" in
  200|302) echo "HTTP $CODE (up)" ;;
  401)     echo "HTTP 401 (up — basic auth challenge, expected)" ;;
  *)       echo "HTTP $CODE — investigate" ;;
esac
