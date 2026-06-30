#!/usr/bin/env bash
# test-autodeploy.sh — pull the latest pushed commit and redeploy the test-machine
# stack, rebuilding ONLY the images whose source actually changed.
#
# Designed to run unattended from cron/systemd on the test box (see
# scripts/install-autodeploy-cron.sh). It is a no-op when the local checkout is
# already at the remote tip, so it is cheap to run every few minutes.
#
# What it does each tick:
#   1. git fetch the tracked branch
#   2. if already up to date → exit 0 (nothing to do)
#   3. record which paths changed, then `git reset --hard` to the remote tip
#      (a deploy target mirrors the remote; tracked-file drift is discarded,
#       gitignored files like .env / cookies.txt are untouched)
#   4. rebuild bot (→ bot+monitor) iff app/ | requirements.txt | Dockerfile changed,
#      rebuild web iff web/ changed
#   5. `docker compose up -d` the test overlay stack (recreates on any compose/image change)
#
# Safe to run concurrently — an flock guard makes overlapping ticks no-ops.
#
# Override via env (or the .env in the repo root, which is sourced if present):
#   DEPLOY_BRANCH    branch to track            (default: current checked-out branch)
#   DEPLOY_REMOTE    git remote                 (default: origin)
#   DEPLOY_SERVICES  services to (re)create     (default: "db bot monitor web")
#   COMPOSE_FILES    -f flags for compose       (default: the test-machine overlay set)
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"   # cron has a minimal PATH

LOG="${AUTODEPLOY_LOG:-$ROOT/backups/autodeploy.log}"
mkdir -p "$(dirname "$LOG")"

log() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG" >&2; }

# ── Single-flight: never let two ticks build at once ────────────────────────
LOCK="$ROOT/backups/.autodeploy.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another autodeploy run holds the lock; skipping this tick"
  exit 0
fi

# Source .env so DEPLOY_* / MONITOR_AUTH_* etc. are available (gitignored, optional).
[ -f "$ROOT/.env" ] && set -a && . "$ROOT/.env" && set +a

REMOTE="${DEPLOY_REMOTE:-origin}"
BRANCH="${DEPLOY_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
SERVICES="${DEPLOY_SERVICES:-db bot monitor web}"
# Default = the exact test-machine launch stack (see docs/test-machine-setup.md +
# project memory). `expose.yml` (web → 0.0.0.0:3000) is test-box-local/untracked, so
# `git reset --hard` leaves it in place; it MUST stay in this list or a redeploy reverts
# web to the dev loopback bind. Override in the box's .env if your overlay set differs.
COMPOSE_FILES="${COMPOSE_FILES:- -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.external-db.yml -f docker-compose.expose.yml -f docker-compose.public-monitor.yml}"

# ── 1. Fetch ────────────────────────────────────────────────────────────────
if ! git fetch --quiet "$REMOTE" "$BRANCH"; then
  log "ERROR: git fetch $REMOTE $BRANCH failed (creds/network?) — leaving stack untouched"
  exit 1
fi

OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse "$REMOTE/$BRANCH")"

if [ "$OLD" = "$NEW" ]; then
  exit 0   # already current — the common, silent case
fi

log "new commit on $REMOTE/$BRANCH: ${OLD:0:9} → ${NEW:0:9}"
CHANGED="$(git diff --name-only "$OLD" "$NEW")"

# ── 2. Sync working tree to the remote tip ──────────────────────────────────
if ! git reset --hard "$NEW" >>"$LOG" 2>&1; then
  log "ERROR: git reset --hard $NEW failed — stack left on ${OLD:0:9}"
  exit 1
fi

# ── 3. Decide what to rebuild from the changed paths ────────────────────────
REBUILD=()
if grep -qE '^(app/|requirements\.txt|Dockerfile)' <<<"$CHANGED"; then
  REBUILD+=(bot)
fi
if grep -qE '^web/' <<<"$CHANGED"; then
  REBUILD+=(web)
fi

if [ "${#REBUILD[@]}" -gt 0 ]; then
  log "rebuilding image(s): ${REBUILD[*]}"
  if ! docker compose $COMPOSE_FILES build "${REBUILD[@]}" >>"$LOG" 2>&1; then
    log "ERROR: docker compose build failed — NOT recreating containers"
    exit 1
  fi
else
  log "no app/ or web/ changes — recreating from current images (compose/config change)"
fi

# ── 4. Recreate the stack (no-op for unchanged services) ────────────────────
if ! docker compose $COMPOSE_FILES up -d $SERVICES >>"$LOG" 2>&1; then
  log "ERROR: docker compose up -d failed"
  exit 1
fi

log "deployed ${NEW:0:9} ✓ (services: $SERVICES)"
