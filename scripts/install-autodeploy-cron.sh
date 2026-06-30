#!/usr/bin/env bash
# install-autodeploy-cron.sh — install/refresh the auto-deploy cron entry on the
# TEST machine, so new pushes to the tracked branch are pulled + redeployed
# automatically. Idempotent: re-running replaces the existing Horyon autodeploy
# line rather than duplicating it. Default cadence: every 5 minutes.
#
# Usage:
#   scripts/install-autodeploy-cron.sh                      # poll every 5 min
#   AUTODEPLOY_CRON="*/2 * * * *" scripts/install-autodeploy-cron.sh   # custom cadence
#   DEPLOY_BRANCH=public-launch-prep scripts/install-autodeploy-cron.sh  # pin branch
#   scripts/install-autodeploy-cron.sh --remove            # uninstall
#
# Prereqs on the test box:
#   * the git remote must be fetchable non-interactively (token in the remote URL,
#     a credential helper, or a read-only deploy key) — cron has no TTY for prompts.
#   * the invoking user must be in the `docker` group (compose build/up run as it).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

SCRIPT="$ROOT/scripts/test-autodeploy.sh"
LOGDIR="$ROOT/backups"
LOG="$LOGDIR/autodeploy.log"
TAG="# horyon-autodeploy"                             # marker to find our line
SCHED="${AUTODEPLOY_CRON:-*/5 * * * *}"

# Pin the branch into the cron line so it survives a detached/odd checkout state.
BRANCH="${DEPLOY_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
LINE="$SCHED DEPLOY_BRANCH=$BRANCH $SCRIPT >> $LOG 2>&1 $TAG"

mkdir -p "$LOGDIR"

# current crontab minus any prior horyon-autodeploy line
CURRENT="$(crontab -l 2>/dev/null | grep -vF "$TAG" || true)"

if [ "${1:-}" = "--remove" ]; then
  printf '%s\n' "$CURRENT" | sed '/^$/d' | crontab -
  echo "✓ removed horyon-autodeploy cron entry"
  exit 0
fi

printf '%s\n%s\n' "$CURRENT" "$LINE" | sed '/^$/d' | crontab -
echo "✓ installed cron entry:"
echo "    $LINE"
echo
echo "Auto-deploy will track '$BRANCH'; log → $LOG"
echo "Tail it with:  tail -f $LOG"
