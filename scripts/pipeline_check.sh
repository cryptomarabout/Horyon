#!/usr/bin/env bash
# pipeline_check.sh — per-artifact status of the daily intelligence pipeline for one date.
# Mechanical companion to /triage: prints pre-formatted facts, makes no judgments.
# Complements status.sh (stack health) — this checks WHAT THE PIPELINE PRODUCED.
#
# Usage: scripts/pipeline_check.sh [YYYY-MM-DD]   (default: today UTC; before 07:00 UTC
#        falls back to yesterday, since today's digest isn't due yet)
set -uo pipefail
cd "$(dirname "$0")/.."

D="${1:-}"
if [ -z "$D" ]; then
  D=$(date -u +%F)
  if [ "$(date -u +%H)" -lt 7 ]; then
    D=$(date -u -d yesterday +%F)
    echo "note: before 07:00 UTC — today's digest not yet due, checking $D instead"
  fi
fi

PSQL() { docker exec horyon-db psql -U crypto -d crypto -tA -F ' | ' -c "$1" 2>&1; }
row() { local out; out=$(PSQL "$1"); [ -n "$out" ] && echo "$out" || echo "MISSING"; }

echo "=== PIPELINE CHECK $D (now $(date -u '+%F %T') UTC) ==="

echo
echo "=== 0. CONTAINERS ==="
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'horyon-' | sort || echo "docker ps failed"

echo
echo "=== 1. DIGEST (crypto_digest) ==="
echo "date | chars | model | trigger | error"
row "SELECT date, length(content), model_used, trigger, COALESCE(error,'-')
     FROM crypto_digest WHERE date='$D' ORDER BY created_at DESC LIMIT 1;"

echo
echo "=== 2. BULLET ANALYSES + SCORES (digest_bullet_analysis) ==="
echo "analyses | scored | top_score | avg_score"
row "SELECT COUNT(*), COUNT(importance_score), COALESCE(MAX(importance_score),0),
     COALESCE(ROUND(AVG(importance_score)),0)
     FROM digest_bullet_analysis WHERE digest_date='$D';"

echo
echo "=== 3. ENTITY BRIEFS (entity_intel_brief) ==="
echo "for_this_date | total | no_bullet_char(drift) | last_update"
row "SELECT COUNT(*) FILTER (WHERE digest_date='$D'), COUNT(*),
     COUNT(*) FILTER (WHERE position('•' in brief_html) = 0), MAX(updated_at)
     FROM entity_intel_brief;"

echo
echo "=== 4. NARRATIVES ==="
echo "total | on_board(non-dormant) | last_rebuild"
row "SELECT COUNT(*), COUNT(*) FILTER (WHERE state <> 'dormant'), MAX(updated_at) FROM narratives;"

echo
echo "=== 5. THREAD (digest_threads) ==="
echo "status | tweets | model | created"
row "SELECT status, jsonb_array_length(tweets), model_used, created_at
     FROM digest_threads WHERE digest_date='$D';"

echo
echo "=== 6. AUDIO (digest_audio — expect 3 variants, durations short<standard<explainer) ==="
echo "variant | status | secs | words | has_audio"
row "SELECT variant, status, duration_sec, word_count, (audio IS NOT NULL)
     FROM digest_audio WHERE digest_date='$D'
     ORDER BY CASE variant WHEN 'short' THEN 1 WHEN 'standard' THEN 2 ELSE 3 END;"

echo
echo "=== 7. OG CARD (digest_og) ==="
echo "bytes | rendered_from | created"
row "SELECT byte_size, source_at, created_at FROM digest_og WHERE digest_date='$D';"

echo
echo "=== 8. WEEKLY (latest weekly_digest) ==="
echo "week_start | week_end | chars | rotation | error"
row "SELECT week_start, week_end, length(content), rotation, COALESCE(error,'-')
     FROM weekly_digest ORDER BY week_start DESC LIMIT 1;"

echo
echo "=== 9. CRON FRESHNESS (last write per feed vs expected cadence) ==="
PSQL "SELECT 'ingest_run (20min)',     MAX(started_at)::text  FROM ingest_run
UNION ALL SELECT 'feed_items any',     MAX(ingested_at)::text FROM feed_items
UNION ALL SELECT 'kaiko items (6h)',   MAX(ingested_at)::text FROM feed_items WHERE creator='Kaiko'
UNION ALL SELECT 'podcasts (6h)',      MAX(COALESCE(summarized_at, fetched_at))::text FROM podcast_episodes
UNION ALL SELECT 'protocol TVL (2h)',  MAX(fetched_at)::text  FROM defillama_protocols
UNION ALL SELECT 'coingecko (2h)',     MAX(fetched_at)::text  FROM coingecko_market
UNION ALL SELECT 'chain TVL (daily)',  MAX(date)::text        FROM defillama_tvl
UNION ALL SELECT 'entity_edges (6h)',  MAX(last_seen)::text   FROM entity_edges
UNION ALL SELECT 'avatars (24h)',      MAX(fetched_at)::text  FROM entity_avatars
UNION ALL SELECT 'governance (30min)', MAX(fetched_at)::text  FROM governance_proposals;"

echo
echo "=== 10. BLOCKED / FAILED BACKLOG (last 7 days) ==="
OUT=$(PSQL "SELECT digest_date, 'thread', status FROM digest_threads
            WHERE status='blocked' AND digest_date > current_date - 7
     UNION ALL SELECT digest_date, 'audio/'||variant, status FROM digest_audio
            WHERE status IN ('blocked','failed') AND digest_date > current_date - 7
     UNION ALL SELECT published_at::date, 'podcast', status FROM podcast_episodes
            WHERE status='failed' AND published_at > now() - interval '7 days'
     ORDER BY 1 DESC;")
[ -n "$OUT" ] && echo "$OUT" || echo "none"

echo
echo "=== 11. BOT ERRORS (last 2000 log lines) ==="
ERRS=$(docker compose logs --tail=2000 bot 2>&1 | grep -E '(ERROR|Traceback|CRITICAL)' | tail -20)
[ -n "$ERRS" ] && echo "$ERRS" || echo "none"

echo
echo "=== 12. FEED SOURCE HEALTH (>2 consecutive failures) ==="
FAILS=$(PSQL "SELECT url, consecutive_failures FROM source_health
              WHERE consecutive_failures > 2 ORDER BY consecutive_failures DESC LIMIT 10;")
[ -n "$FAILS" ] && echo "$FAILS" || echo "all sources healthy"
