#!/usr/bin/env bash
# docker-cleanup.sh — reclaim Docker disk SAFELY on the small 29G root disk.
#
# Why: build cache + dangling <none> layers regrow on every bot/web rebuild (the cache
# hit ~3.3GB once) and will fill the disk. Stale *tagged* images pile up too — e.g. an old
# node:18-alpine left behind after the web base bumped to node:20.
#
# Modes:
#   (no args)   cron-safe sweep — build cache + dangling <none> images + stopped-container
#               leftovers. NEVER removes a tagged image.
#   --deep      ALSO remove stale TAGGED images that nothing references: not used by any
#               container (running or stopped), not a build-stage base (FROM in a tracked
#               Dockerfile), not named in a compose file, and not run by a scripts/ tool.
#               This is the smart bit — plain `docker image prune -a` would also delete live
#               build bases (node:20-alpine, python:3.12-slim) and idle tool images
#               (sonarsource/sonar-scanner-cli) because no *container* runs them, forcing a
#               multi-GB re-pull on the next build/scan. We keep those on purpose.
#               `docker rmi` (non-force) is a second backstop: it refuses any image a
#               container is using, so even a hole in the keep-list can't nuke a live image.
#   --dry-run   with --deep, print what WOULD be removed and exit without deleting.
#
# Run by hand anytime disk is tight; the default mode is what the weekly host cron calls.
set -euo pipefail

DEEP=0; DRY=0
for a in "$@"; do
  case "$a" in
    --deep)    DEEP=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "usage: $0 [--deep] [--dry-run]" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"

avail_bytes() { df -B1 --output=avail / | tail -1 | tr -d ' '; }
human()       { numfmt --to=iec --suffix=B "${1:-0}" 2>/dev/null || echo "${1:-0}B"; }

before=$(avail_bytes)
tag=""; [[ $DEEP -eq 1 ]] && tag=" (deep)"; [[ $DRY -eq 1 ]] && tag="$tag [dry-run]"
echo "=== docker-cleanup $(date -u +%FT%TZ)$tag ==="
df -h / | awk 'NR==1 || /\//{print}' | head -2

# ── Always-safe prunes ──────────────────────────────────────────────────────
docker builder prune -af  >/dev/null 2>&1 && echo "✓ pruned build cache"
docker image prune -f     >/dev/null 2>&1 && echo "✓ pruned dangling images"
docker container prune -f >/dev/null 2>&1 && echo "✓ pruned stopped containers"

# ── Deep: remove stale tagged images nothing references ──────────────────────
if [[ $DEEP -eq 1 ]]; then
  # Normalise an image ref to repo:tag (append :latest when untagged) for exact matching.
  norm() { sed -E 's/[[:space:]]//g; /^$/d; /:/!s/$/:latest/'; }

  # Build-stage bases referenced by a tracked Dockerfile (the first non-flag token after
  # FROM — skips `--platform=…` and stage aliases like `AS builder`). If this comes back
  # empty we can't safely tell a base from junk, so we bail on image removal (fail-safe).
  from_bases="$(git ls-files -z '*Dockerfile*' 2>/dev/null \
    | xargs -0r grep -hiE '^[[:space:]]*FROM[[:space:]]' 2>/dev/null \
    | awk '{for(i=2;i<=NF;i++){if($i !~ /^--/){print $i; break}}}' \
    | grep -viE '^(scratch|builder|runner|base)$' | norm | sort -u)"

  if [[ -z "$from_bases" ]]; then
    echo "⚠ could not read Dockerfile FROM bases (not a git checkout?) — skipping image sweep"
  else
    # Keep-list = container images + FROM bases + compose images + scripts' docker-run images.
    keep="$(
      {
        docker ps -a --format '{{.Image}}'
        printf '%s\n' "$from_bases"
        git ls-files -z 'docker-compose*.yml' 2>/dev/null | xargs -0r \
          grep -hE '^[[:space:]]*image:' 2>/dev/null | sed -E "s/^[[:space:]]*image:[[:space:]]*//; s/[\"']//g"
        # Namespaced images run from tool scripts (e.g. sonarsource/sonar-scanner-cli:latest)
        git ls-files -z 'scripts/*' 2>/dev/null | xargs -0r \
          grep -hoE '[a-z0-9][a-z0-9._-]*/[a-z0-9._-]+:[a-z0-9._-]+' 2>/dev/null
      } | norm | sort -u
    )"

    mapfile -t candidates < <(
      docker images --format '{{.Repository}}:{{.Tag}}' \
        | grep -vF '<none>' \
        | grep -vxF -f <(printf '%s\n' "$keep")
    )

    if [[ ${#candidates[@]} -eq 0 ]]; then
      echo "✓ no stale tagged images to remove"
    else
      for img in "${candidates[@]}"; do
        if [[ $DRY -eq 1 ]]; then
          echo "would remove: $img"
        elif docker rmi "$img" >/dev/null 2>&1; then
          echo "✓ removed $img"
        else
          echo "· kept $img (still in use)"
        fi
      done
    fi
  fi
fi

echo "--- after ---"
df -h / | awk '/\//{print}' | head -1
echo "reclaimed: $(human $(( $(avail_bytes) - before )))"
docker system df
