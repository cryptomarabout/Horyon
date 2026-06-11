#!/usr/bin/env bash
# deploy.sh — build → restart → wait-healthy → tail logs for the bot or web stack.
# Does the mechanical work; exits non-zero on build failure or unhealthy so the
# caller (the /deploy-bot or /deploy-web skill, or a human) can react.
#
# Usage:
#   scripts/deploy.sh bot     # rebuild app/ image, restart bot + monitor
#   scripts/deploy.sh web     # rebuild web/ image, restart web
set -uo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-}"
case "$TARGET" in
  bot) SERVICES="bot monitor"; CONTAINERS="horyon-bot horyon-monitor"; HEALTH_TIMEOUT=60 ;;
  web) SERVICES="web";         CONTAINERS="horyon-web";                HEALTH_TIMEOUT=90 ;;
  *) echo "deploy.sh: target must be 'bot' or 'web'." >&2; exit 2 ;;
esac

echo "=== BUILD ($TARGET) ==="
if ! docker compose build "$( [ "$TARGET" = bot ] && echo bot || echo web )"; then
  echo "deploy.sh: BUILD FAILED — not restarting." >&2
  exit 1
fi

echo
echo "=== RESTART ($SERVICES) ==="
docker compose up -d $SERVICES

echo
echo "=== WAIT FOR HEALTHY (timeout ${HEALTH_TIMEOUT}s) ==="
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
unhealthy=0
while :; do
  all_ok=1
  for c in $CONTAINERS; do
    h=$(docker inspect "$c" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo missing)
    printf '  %-16s %s\n' "$c" "$h"
    [ "$h" = "unhealthy" ] && unhealthy=1
    [ "$h" != "healthy" ] && all_ok=0
  done
  { [ "$all_ok" = 1 ] || [ "$unhealthy" = 1 ] || [ "$(date +%s)" -ge "$deadline" ]; } && break
  sleep 5
  echo "  ---"
done

echo
echo "=== LOGS (last 60 lines) ==="
docker compose logs --tail=60 $SERVICES 2>&1 | grep -E '(ERROR|Traceback|Exception|entity extraction|Scheduler started|Application started|inserted|healthy)' | tail -25 \
  || docker compose logs --tail=30 $SERVICES

echo
if [ "$unhealthy" = 1 ]; then
  echo "RESULT: UNHEALTHY — see full logs: docker compose logs $SERVICES" >&2
  exit 1
elif [ "$all_ok" = 1 ]; then
  echo "RESULT: healthy ✓"
else
  echo "RESULT: timed out waiting for healthy (still starting?)" >&2
  exit 1
fi
