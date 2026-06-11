#!/usr/bin/env bash
# logs.sh — tail/filter logs from a stack container. Pure dispatch; no Claude
# needed to read the output. Replaces the old /logs skill.
#
# Usage:
#   scripts/logs.sh [service] [pattern]
#     service — bot|web|monitor|caddy|db (aliases b/w/m/c/d). Default: bot.
#     pattern — optional case-insensitive grep over the last 500 lines.
#
#   scripts/logs.sh                 # last 100 lines from bot
#   scripts/logs.sh web             # last 100 lines from web
#   scripts/logs.sh bot ERROR       # error lines from bot
#   scripts/logs.sh -f bot          # follow (passes -f through; Ctrl-C to stop)
set -euo pipefail
cd "$(dirname "$0")/.."

FOLLOW=""
if [ "${1:-}" = "-f" ]; then FOLLOW="-f"; shift; fi

case "${1:-bot}" in
  b|bot)     SVC=bot;     CTR=horyon-bot ;;
  w|web)     SVC=web;     CTR=horyon-web ;;
  m|monitor) SVC=monitor; CTR=horyon-monitor ;;
  c|caddy)   SVC=caddy;   CTR=horyon-caddy ;;
  d|db)      SVC=db;      CTR=horyon-db ;;
  *) echo "logs.sh: unknown service '${1}'. Use bot|web|monitor|caddy|db." >&2; exit 2 ;;
esac
PATTERN="${2:-}"

STATE=$(docker inspect "$CTR" --format '{{.State.Status}}' 2>/dev/null || echo "missing")
if [ "$STATE" != "running" ]; then
  echo "logs.sh: container $CTR is '$STATE' (not running)." >&2
  exit 1
fi

if [ -n "$PATTERN" ]; then
  if docker compose logs --tail=500 "$SVC" 2>&1 | grep -i --color=never "$PATTERN"; then
    :
  else
    echo "No matches for '$PATTERN' in last 500 lines of $SVC."
  fi
else
  docker compose logs --tail=100 $FOLLOW "$SVC"
fi
