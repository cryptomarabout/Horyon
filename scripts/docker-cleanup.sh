#!/usr/bin/env bash
# docker-cleanup.sh — reclaim Docker disk SAFELY on a schedule.
#
# Why: the build cache and dangling/untagged image layers regrow every time we
# rebuild bot/web (the cache hit ~3.3GB once). Left unattended they fill the small
# 29G root disk. This prunes the safe-to-drop stuff only.
#
# What it prunes (and what it deliberately does NOT):
#   • build cache             → all of it (rebuilds just repopulate it)
#   • dangling images         → untagged <none> layers from old builds
#   • stopped-container leftovers (-f, no -a) — NEVER touches tagged images that a
#     running OR stopped compose service could reference, so base images
#     (python:3.12-slim, node, pgvector, caddy, sonarqube…) survive and we never
#     force a multi-GB re-pull. Use `docker image prune -a` by hand if you want that.
set -euo pipefail

echo "=== docker-cleanup $(date -u +%FT%TZ) ==="
df -h / | awk 'NR==1 || /\//{print}' | head -2

docker builder prune -af >/dev/null 2>&1 && echo "✓ pruned build cache"
docker image prune -f   >/dev/null 2>&1 && echo "✓ pruned dangling images"
docker container prune -f >/dev/null 2>&1 && echo "✓ pruned stopped containers"

echo "--- after ---"
df -h / | awk '/\//{print}' | head -1
docker system df
