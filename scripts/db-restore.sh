#!/usr/bin/env bash
# db-restore.sh — restore a Horyon DB from the offsite backup repo.
#
# Companions: scripts/db-backup.sh (writes the backups) and the monitor page
# data-switch panel (triggers restores from the UI in dev mode).
#
# Usage:
#   scripts/db-restore.sh --list               # show available backup dates
#   scripts/db-restore.sh --latest             # restore most recent backup
#   scripts/db-restore.sh --date 2026-06-24    # restore specific date
#   scripts/db-restore.sh --pull               # just clone/pull the backup repo (no restore)
#   scripts/db-restore.sh --wipe               # truncate all tables; schema stays intact
#
# Prerequisites:
#   • BACKUP_REPO + BACKUP_REPO_TOKEN in .env (same as db-backup.sh)
#   • horyon-db container running
#   • The local backup clone is kept at ${BACKUP_WORKDIR:-~/.horyon-db-backups}
#     and is also what docker-compose.dev.yml mounts into the monitor container.
#
# Each dump is a pg_dump custom-format file split into <100MB parts (GitHub limit).
# Parts are named crypto-YYYY-MM-DD.dump.partNNN; they are concatenated in order
# before being piped to pg_restore.

set -euo pipefail
cd "$(dirname "$0")/.."

# ── Parse args ───────────────────────────────────────────────────────────────
ACTION=""
RESTORE_DATE=""
for arg in "$@"; do
  case "$arg" in
    --list)        ACTION=list ;;
    --latest)      ACTION=latest ;;
    --pull)        ACTION=pull ;;
    --wipe)        ACTION=wipe ;;
    --date)        ;;  # key; value follows
    --help|-h)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *)
      # Treat as date value if previous arg was --date, else unknown
      if [[ "$arg" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        ACTION=date
        RESTORE_DATE="$arg"
      else
        echo "db-restore.sh: unknown argument: $arg" >&2
        exit 2
      fi
      ;;
  esac
done

if [ -z "$ACTION" ]; then
  echo "db-restore.sh: specify an action. Try --help." >&2
  exit 2
fi

# ── Read env ─────────────────────────────────────────────────────────────────
env_val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }

POSTGRES_PASSWORD="$(env_val POSTGRES_PASSWORD)"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD missing from .env}"

WORKDIR="${BACKUP_WORKDIR:-$HOME/.horyon-db-backups}"

# ── Wipe action (no backup repo needed) ──────────────────────────────────────
if [ "$ACTION" = wipe ]; then
  echo "=== Wipe: truncating all tables in horyon-db ==="
  echo "  (Schema is preserved; all row data is deleted.)"
  read -r -p "  This is destructive. Type 'wipe' to confirm: " confirm
  [ "$confirm" = wipe ] || { echo "  Aborted."; exit 0; }

  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" horyon-db psql -U crypto -d crypto -c "
    DO \$\$
    DECLARE r RECORD;
    BEGIN
        FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
            EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' CASCADE';
        END LOOP;
    END \$\$;
  "
  echo "  ✓ All tables truncated."
  exit 0
fi

# ── Backup repo operations need credentials ───────────────────────────────────
BACKUP_REPO="$(env_val BACKUP_REPO)"
BACKUP_REPO_TOKEN="$(env_val BACKUP_REPO_TOKEN)"

if [ -z "$BACKUP_REPO" ] || [ -z "$BACKUP_REPO_TOKEN" ]; then
  echo "db-restore.sh: BACKUP_REPO and BACKUP_REPO_TOKEN must be set in .env" >&2
  exit 1
fi

BRANCH="${BACKUP_BRANCH:-main}"
REMOTE="https://x-access-token:${BACKUP_REPO_TOKEN}@github.com/${BACKUP_REPO}.git"

# ── Clone or pull the backup repo ────────────────────────────────────────────
_pull_repo() {
  if [ ! -d "$WORKDIR/.git" ]; then
    echo "  → cloning $BACKUP_REPO → $WORKDIR"
    mkdir -p "$WORKDIR"
    git clone "$REMOTE" "$WORKDIR" 2>&1 | grep -v 'empty repository' || true
    cd "$WORKDIR"
  else
    cd "$WORKDIR"
    git remote set-url origin "https://github.com/${BACKUP_REPO}.git" 2>/dev/null || true
  fi
  git config user.name  "horyon-restore"
  git config user.email "restore@horyon.local"
  git checkout -B "$BRANCH" >/dev/null 2>&1 || true
  echo "  → pulling latest from $BACKUP_REPO"
  git pull --quiet --ff-only "$REMOTE" "$BRANCH" 2>/dev/null || true
}

if [ "$ACTION" = pull ]; then
  echo "=== Pulling backup repo ==="
  _pull_repo
  echo
  echo "Available backups:"
  ls -1 "$WORKDIR"/crypto-*.dump.part000 2>/dev/null \
    | sed -E 's#.*/crypto-([0-9-]+)\.dump\.part000$#  \1#' | sort -r \
    || echo "  (none yet)"
  exit 0
fi

# ── List action ──────────────────────────────────────────────────────────────
if [ "$ACTION" = list ]; then
  echo "=== Available backups ==="
  if [ ! -d "$WORKDIR" ]; then
    echo "  Backup dir not found: $WORKDIR"
    echo "  Run: scripts/db-restore.sh --pull"
    exit 0
  fi
  found=0
  while IFS= read -r part; do
    d=$(basename "$part" | sed -E 's/crypto-([0-9-]+)\.dump\.part000/\1/')
    # total size of all parts for this date
    sz=$(du -sh "$WORKDIR"/crypto-"$d".dump.part* 2>/dev/null | awk '{sum+=$1} END{print sum}')
    nparts=$(ls "$WORKDIR"/crypto-"$d".dump.part* 2>/dev/null | wc -l | tr -d ' ')
    printf "  %s  (%s parts, %sMB total)\n" "$d" "$nparts" "$sz"
    found=1
  done < <(ls "$WORKDIR"/crypto-*.dump.part000 2>/dev/null | sort -r)
  [ "$found" = 0 ] && echo "  (no backups — run: scripts/db-restore.sh --pull)"
  exit 0
fi

# ── Resolve date ─────────────────────────────────────────────────────────────
echo "=== Preparing restore ==="
_pull_repo
cd "$WORKDIR"

if [ "$ACTION" = latest ]; then
  RESTORE_DATE=$(ls crypto-*.dump.part000 2>/dev/null \
    | sed -E 's/crypto-([0-9-]+)\.dump\.part000/\1/' | sort -r | head -1)
  if [ -z "$RESTORE_DATE" ]; then
    echo "  ERROR: no backups found in $WORKDIR" >&2
    exit 1
  fi
  echo "  Using latest: $RESTORE_DATE"
fi

# Validate
PARTS=( $(ls "crypto-${RESTORE_DATE}.dump.part"* 2>/dev/null | sort) )
if [ ${#PARTS[@]} -eq 0 ]; then
  echo "  ERROR: no parts found for date $RESTORE_DATE" >&2
  exit 1
fi

TOTAL_SIZE=$(du -sh "${PARTS[@]}" | awk '{sum+=$1} END{printf "%.0fMB", sum}')
echo "  Date:  $RESTORE_DATE"
echo "  Parts: ${#PARTS[@]}  ($TOTAL_SIZE)"
echo
read -r -p "  This will REPLACE the current horyon-db. Type 'restore' to confirm: " confirm
[ "$confirm" = restore ] || { echo "  Aborted."; exit 0; }

# ── Restore ──────────────────────────────────────────────────────────────────
echo
echo "=== Restoring $RESTORE_DATE → horyon-db ==="
cat "${PARTS[@]}" \
  | docker exec -i \
      -e PGPASSWORD="$POSTGRES_PASSWORD" \
      horyon-db \
      pg_restore -U crypto -d crypto \
        --clean --if-exists \
        --no-owner --no-privileges \
        --exit-on-error

echo
echo "✓ Restore complete: $RESTORE_DATE"
echo "  Restart bot+web to refresh their in-memory state:"
echo "    docker compose restart bot web"
