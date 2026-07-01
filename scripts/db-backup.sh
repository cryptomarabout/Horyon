#!/usr/bin/env bash
# db-backup.sh — daily offsite backup of the Horyon Postgres DB to a SEPARATE
# private GitHub repo, so we never lose data even if this host dies.
#
# Why a separate repo (not this one): this repo's .gitignore forbids committing
# backups, and it is the private source that feeds the PUBLIC mirror via
# scripts/publish-public.sh. Binary dumps would bloat its history forever and add
# risk to the public-publish path. Backups live in their own private repo.
#
# What it does: pg_dump (custom/compressed format) of the `crypto` DB → one dated
# file per day → commit + push. Each dump is a COMPLETE standalone snapshot of the
# whole DB, so any single one fully restores everything.
#
# Retention — keep only the last BACKUP_KEEP dumps (default 2), and store NOTHING
# on this machine:
#   • The repo is checked out into an EPHEMERAL temp dir that is deleted on exit
#     (trap), so no dumps linger on the host disk (a 29G root disk fills fast).
#   • We shallow-clone the backup repo to fetch the current dumps, add today's,
#     prune all but the newest BACKUP_KEEP dates, then publish as a SINGLE fresh
#     orphan commit + force-push. So the remote always holds exactly the last
#     BACKUP_KEEP dumps with no history bloat — old snapshots are never stored,
#     on the machine or in git history. Pruning happens only after today's dump
#     is in the tree we push, so we never drop old data before new is offsite.
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
#   (or just use scripts/db-restore.sh --latest)
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

BRANCH="${BACKUP_BRANCH:-main}"
KEEP="${BACKUP_KEEP:-2}"                                 # dumps to retain on the repo
REMOTE="https://x-access-token:${BACKUP_REPO_TOKEN}@github.com/${BACKUP_REPO}.git"
CLEANURL="https://github.com/${BACKUP_REPO}.git"
STAMP="$(date -u +%Y-%m-%d)"
DUMPFILE="crypto-${STAMP}.dump"

# --- ephemeral working dir: nothing is left on this machine ---------------------
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/horyon-backup.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

# --- shallow-clone the backup repo to pick up the current (retained) dumps ------
# --depth 1 keeps the fetch tiny; history has no value (dumps are independent).
echo "→ fetching current backups from $BACKUP_REPO"
git clone --depth 1 "$REMOTE" "$WORKDIR" 2>&1 | grep -v 'empty repository' || true
cd "$WORKDIR"
git init -q 2>/dev/null || true                          # no-op if clone gave us .git
git remote set-url origin "$CLEANURL" 2>/dev/null \
  || git remote add origin "$CLEANURL" 2>/dev/null || true
git config user.name  "horyon-backup"
git config user.email "backup@horyon.local"

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

# --- retention: keep only the newest KEEP dump dates (today's included) --------
# We prune BEFORE publishing, so the tree we push holds exactly the last KEEP
# dumps. Today's dump is already written above, so old data is never dropped
# before the new snapshot is in the commit we push.
mapfile -t DATES < <(ls "$WORKDIR"/crypto-*.dump.part* 2>/dev/null \
  | sed -E 's#.*/crypto-([0-9-]+)\.dump\.part[0-9]+$#\1#' | sort -ru | uniq)
if [ "${#DATES[@]}" -gt "$KEEP" ]; then
  for d in "${DATES[@]:$KEEP}"; do
    rm -f "$WORKDIR/crypto-$d.dump.part"*
    echo "  – dropped old dump crypto-$d (keeping newest $KEEP)"
  done
fi

# --- publish as ONE fresh orphan commit + force-push --------------------------
# The remote therefore always holds just the last KEEP dumps, with no accumulated
# history (each dump is an independent full snapshot — past commits carry no value).
git checkout -q --orphan _publish 2>/dev/null || git checkout -q -B _publish
git add -A
NKEPT="$(ls "$WORKDIR"/crypto-*.dump.part000 2>/dev/null | wc -l)"
git commit -q -m "DB backups — last $KEEP dumps (through $STAMP, $SIZE latest)"
git branch -q -D "$BRANCH" 2>/dev/null || true
git branch -qm "$BRANCH"
git push --force --quiet "$REMOTE" "$BRANCH"
echo "✓ pushed $DUMPFILE ($SIZE, $NPARTS parts); repo now holds $NKEPT dump(s), last $KEEP retained"
# WORKDIR (and every dump) is removed by the EXIT trap — nothing persists locally.
