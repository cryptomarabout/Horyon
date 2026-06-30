#!/usr/bin/env bash
# db-backup.sh — daily offsite backup of the Horyon Postgres DB to a SEPARATE
# private GitHub repo, so we never lose data even if this host dies.
#
# Why a separate repo (not this one): this repo's .gitignore forbids committing
# backups, and it is the private source that feeds the PUBLIC mirror via
# scripts/publish-public.sh. Binary dumps would bloat its history forever and add
# risk to the public-publish path. Backups live in their own private repo, with a
# local clone kept OUTSIDE this working tree.
#
# What it does: pg_dump (custom/compressed format) of the `crypto` DB → one dated
# file per day → commit + push. Each dump is a COMPLETE standalone snapshot of the
# whole DB, so any single one fully restores everything. Retention keeps the last
# BACKUP_KEEP_DAYS dumps (default 14): older ones are pruned ONLY AFTER the new dump
# is committed + pushed, so we never erase old data before new data is safely
# offsite. When anything is pruned, history is squashed + force-pushed so the local
# clone AND the GitHub remote both stay small (old commit history has no value —
# the dumps are independent full snapshots).
#
# ── One-time setup ──────────────────────────────────────────────────────────
#   1. Create an EMPTY private GitHub repo, e.g.  cryptomarabout/horyon-db-backups
#   2. Create a *fine-grained* PAT scoped to ONLY that repo, with
#        Repository permissions → Contents: Read and write
#   3. Add two lines to .env (in this repo root; .env is gitignored):
#        BACKUP_REPO=cryptomarabout/horyon-db-backups
#        BACKUP_REPO_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxx
#   4. Run once to verify:  scripts/db-backup.sh
#
# ── Cron (installed by scripts/install-db-backup-cron.sh) ───────────────────
#   30 3 * * *  /path/to/horyon/scripts/db-backup.sh \
#               >> /path/to/horyon/backups/backup.log 2>&1
#
# Dumps are split into <100MB parts (crypto-DATE.dump.partNNN) because GitHub
# hard-rejects any single file over 100MB. Restore concatenates them back.
#
# ── Restore (on any host with the pgvector image) ──────────────────────────
#   git clone https://github.com/cryptomarabout/horyon-db-backups
#   cd horyon-db-backups
#   cat crypto-YYYY-MM-DD.dump.part* \
#     | docker exec -i horyon-db pg_restore -U crypto -d crypto --clean --if-exists
#
set -euo pipefail
cd "$(dirname "$0")/.."

# --- read just the keys we need from .env (don't source the whole file: it has
#     values with $$, hashes, etc. that would break under shell sourcing) ------
env_val() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

BACKUP_REPO="$(env_val BACKUP_REPO)"
BACKUP_REPO_TOKEN="$(env_val BACKUP_REPO_TOKEN)"
POSTGRES_PASSWORD="$(env_val POSTGRES_PASSWORD)"

: "${BACKUP_REPO:?set BACKUP_REPO=owner/repo in .env}"
: "${BACKUP_REPO_TOKEN:?set BACKUP_REPO_TOKEN=<fine-grained PAT> in .env}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD missing from .env}"

WORKDIR="${BACKUP_WORKDIR:-$HOME/.horyon-db-backups}"   # local clone, outside this repo
BRANCH="${BACKUP_BRANCH:-main}"
REMOTE="https://x-access-token:${BACKUP_REPO_TOKEN}@github.com/${BACKUP_REPO}.git"
STAMP="$(date -u +%Y-%m-%d)"
DUMPFILE="crypto-${STAMP}.dump"

# --- clone the backup repo on first run (handles an empty new repo) -----------
if [ ! -d "$WORKDIR/.git" ]; then
  echo "→ cloning $BACKUP_REPO → $WORKDIR"
  git clone "$REMOTE" "$WORKDIR" 2>&1 | grep -v 'empty repository' || true
fi
cd "$WORKDIR"
# keep the token OUT of on-disk .git/config — we push with an explicit URL below
git remote set-url origin "https://github.com/${BACKUP_REPO}.git" 2>/dev/null || true
git config user.name  "horyon-backup"
git config user.email "backup@horyon.local"
git checkout -B "$BRANCH" >/dev/null 2>&1 || true
# fast-forward to remote if it already has this branch (single-writer, best-effort)
git pull --quiet --ff-only "$REMOTE" "$BRANCH" 2>/dev/null || true

# --- dump (atomic via .tmp; PGPASSWORD so it works regardless of pg_hba) -------
echo "→ dumping crypto DB → $DUMPFILE"
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" horyon-db \
  pg_dump -U crypto -d crypto -Fc > "$WORKDIR/$DUMPFILE.tmp"
mv "$WORKDIR/$DUMPFILE.tmp" "$WORKDIR/$DUMPFILE"
SIZE="$(du -h "$WORKDIR/$DUMPFILE" | cut -f1)"

# --- split into <100MB parts (GitHub rejects single files >100MB) -------------
PREFIX="${DUMPFILE}.part"
rm -f "$WORKDIR/${PREFIX}"*                              # clear today's prior parts
split -b 90M -d -a 3 "$WORKDIR/$DUMPFILE" "$WORKDIR/$PREFIX"
rm -f "$WORKDIR/$DUMPFILE"                               # keep only the parts in git
NPARTS="$(ls "$WORKDIR/${PREFIX}"* | wc -l)"

# --- commit + push (one dump per UTC day; reruns same day just refresh it) ----
git add -A
if git diff --cached --quiet; then
  echo "✓ $DUMPFILE unchanged — nothing to commit"
  exit 0
fi
git commit -q -m "DB backup $STAMP ($SIZE, $NPARTS parts)"
git push --quiet "$REMOTE" "$BRANCH"
echo "✓ pushed $DUMPFILE ($SIZE, $NPARTS parts) to $BACKUP_REPO"

# --- retention: keep only the last KEEP_DAYS full dumps -----------------------
# Runs ONLY after the new dump is safely committed + pushed above, so old data is
# never deleted before the new snapshot is offsite. Each dump is a complete DB
# snapshot, so pruning older ones loses no recoverability while the window holds.
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
# distinct dump dates currently tracked, newest first
mapfile -t DATES < <(ls "$WORKDIR"/crypto-*.dump.part* 2>/dev/null \
  | sed -E 's#.*/crypto-([0-9-]+)\.dump\.part[0-9]+$#\1#' | sort -ru | uniq)
if [ "${#DATES[@]}" -gt "$KEEP_DAYS" ]; then
  for d in "${DATES[@]:$KEEP_DAYS}"; do
    git rm -q "crypto-$d.dump.part"*
    echo "  – pruned old dump crypto-$d"
  done
  # Squash to a single fresh commit so the dropped dumps leave .git AND the remote
  # (they're independent full snapshots — past commit history carries no value).
  git checkout -q --orphan _retention
  git add -A
  git commit -q -m "DB backups — last $KEEP_DAYS full dumps (squashed $STAMP)"
  git branch -q -D "$BRANCH" 2>/dev/null || true
  git branch -qm "$BRANCH"
  git push --force --quiet "$REMOTE" "$BRANCH"
  git gc --prune=now --quiet
  echo "✓ retention: kept last $KEEP_DAYS dumps; squashed history + force-pushed; reclaimed local .git"
fi
