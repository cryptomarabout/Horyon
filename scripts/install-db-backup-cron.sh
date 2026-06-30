#!/usr/bin/env bash
# install-db-backup-cron.sh — install/refresh the daily DB-backup cron entry for
# the current user. Idempotent: re-running replaces the existing Horyon backup
# line rather than duplicating it. Default schedule: 03:30 UTC daily.
#
# Usage:
#   scripts/install-db-backup-cron.sh            # install at 03:30 UTC
#   BACKUP_CRON="0 4 * * *" scripts/install-db-backup-cron.sh   # custom schedule
#   scripts/install-db-backup-cron.sh --remove   # uninstall
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

SCRIPT="$ROOT/scripts/db-backup.sh"
LOGDIR="$ROOT/backups"
LOG="$LOGDIR/backup.log"
TAG="# horyon-db-backup"                              # marker to find our line
SCHED="${BACKUP_CRON:-30 3 * * *}"
LINE="$SCHED $SCRIPT >> $LOG 2>&1 $TAG"

mkdir -p "$LOGDIR"

# current crontab minus any prior horyon-db-backup line
CURRENT="$(crontab -l 2>/dev/null | grep -vF "$TAG" || true)"

if [ "${1:-}" = "--remove" ]; then
  printf '%s\n' "$CURRENT" | crontab -
  echo "✓ removed horyon-db-backup cron entry"
  exit 0
fi

printf '%s\n%s\n' "$CURRENT" "$LINE" | sed '/^$/d' | crontab -
echo "✓ installed cron entry:"
echo "    $LINE"
echo
echo "Backups will run daily; log → $LOG"
