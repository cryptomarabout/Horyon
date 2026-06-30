#!/usr/bin/env bash
# bootstrap.sh — first-time setup on a new machine.
#
# What it does:
#   1. Checks prerequisites (docker, docker compose, git)
#   2. Copies .env.example → .env if no .env exists (then pauses for you to fill it in)
#   3. Builds bot + web images
#   4. Starts db, waits for it to be healthy
#   5. Applies schema.sql (idempotent)
#   6. (Optional) provisions the horyon_web least-privilege role
#   7. Starts remaining services with the dev overlay
#
# Usage:
#   scripts/bootstrap.sh             # full dev stack (polling mode, no Caddy)
#   scripts/bootstrap.sh --prod      # production compose only (no dev overlay)
#   scripts/bootstrap.sh --web-role  # also run deploy/web_db_role.sql (needs WEB_DB_PASSWORD in .env)
set -uo pipefail
cd "$(dirname "$0")/.."

DEV=true
WEB_ROLE=false
for arg in "$@"; do
  case "$arg" in
    --prod)     DEV=false ;;
    --web-role) WEB_ROLE=true ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0 ;;
  esac
done

COMPOSE_FILES="-f docker-compose.yml"
[ "$DEV" = true ] && COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.dev.yml"

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
echo "=== Checking prerequisites ==="

require() {
  if ! command -v "$1" &>/dev/null; then
    echo "ERROR: '$1' not found. $2" >&2; exit 1
  fi
  echo "  ✓ $1"
}

require docker  "Install Docker: https://docs.docker.com/engine/install/"
docker compose version &>/dev/null || { echo "ERROR: 'docker compose' plugin not found." >&2; exit 1; }
echo "  ✓ docker compose"
require git "Install git: https://git-scm.com/downloads"

# Check Ollama (optional — only used by bot/ingest for embeddings)
if curl -sf http://localhost:11434/api/tags &>/dev/null; then
  echo "  ✓ Ollama (localhost:11434)"
else
  echo "  ⚠  Ollama not detected at localhost:11434 — embeddings will be skipped."
  echo "     Install: https://ollama.com/download  then: ollama pull nomic-embed-text"
fi

# ── 2. .env ───────────────────────────────────────────────────────────────────
echo
echo "=== Environment ==="

if [ ! -f .env ]; then
  cp .env.example .env
  echo "  Created .env from .env.example."
  echo
  echo "  ┌──────────────────────────────────────────────────────────────────┐"
  echo "  │  Fill in the required values in .env before continuing:          │"
  echo "  │                                                                  │"
  echo "  │    POSTGRES_PASSWORD      — any strong password                 │"
  echo "  │    WEB_DB_PASSWORD        — any strong password (dev: optional)  │"
  echo "  │    TELEGRAM_BOT_TOKEN     — from @BotFather                      │"
  echo "  │    OPENROUTER_API_KEY     — from openrouter.ai (free tier ok)    │"
  echo "  │                                                                  │"
  echo "  │  Optional: NIM_API_KEY (nvidia), CMC_API_KEY (coinmarketcap)     │"
  echo "  └──────────────────────────────────────────────────────────────────┘"
  echo
  read -r -p "  Press Enter once you've filled in .env (Ctrl-C to abort)..."
else
  echo "  ✓ .env already exists"
fi

# Validate minimum required vars are non-empty
_missing=()
while IFS='=' read -r key val; do
  [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
  val="${val%%#*}"  # strip inline comments
  val="${val//\"/}"
  val="${val// /}"
  case "$key" in
    POSTGRES_PASSWORD|TELEGRAM_BOT_TOKEN|OPENROUTER_API_KEY)
      if [[ -z "$val" || "$val" == CHANGE_ME* ]]; then
        _missing+=("$key")
      fi
      ;;
  esac
done < .env

if [ ${#_missing[@]} -gt 0 ]; then
  echo "  ERROR: still unset in .env: ${_missing[*]}" >&2
  exit 1
fi
echo "  ✓ required vars present"

# ── 3. Build images ───────────────────────────────────────────────────────────
echo
echo "=== Building images ==="
docker compose $COMPOSE_FILES build bot web

# ── 4. Start DB + wait healthy ────────────────────────────────────────────────
echo
echo "=== Starting database ==="
docker compose $COMPOSE_FILES up -d db
echo "  Waiting for horyon-db to be healthy..."
deadline=$(( $(date +%s) + 60 ))
while :; do
  h=$(docker inspect horyon-db --format '{{.State.Health.Status}}' 2>/dev/null || echo missing)
  [ "$h" = "healthy" ] && break
  [ "$(date +%s)" -ge "$deadline" ] && { echo "  ERROR: DB did not become healthy in time." >&2; exit 1; }
  sleep 2
done
echo "  ✓ DB healthy"

# ── 5. Apply schema (idempotent) ──────────────────────────────────────────────
echo
echo "=== Applying schema ==="
docker exec -i horyon-db psql -U crypto -d crypto < deploy/schema.sql
echo "  ✓ schema.sql applied"

# ── 6. horyon_web role (optional, required for --prod or --web-role) ──────────
if [ "$WEB_ROLE" = true ] || [ "$DEV" = false ]; then
  echo
  echo "=== Provisioning horyon_web DB role ==="
  # Load WEB_DB_PASSWORD from .env
  WEB_DB_PASSWORD=$(grep -E '^WEB_DB_PASSWORD=' .env | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
  if [ -z "$WEB_DB_PASSWORD" ] || [[ "$WEB_DB_PASSWORD" == CHANGE_ME* ]]; then
    echo "  ERROR: WEB_DB_PASSWORD not set in .env — set it and re-run with --web-role." >&2
    exit 1
  fi
  docker exec -i horyon-db psql -U crypto -d crypto -v "web_pw=$WEB_DB_PASSWORD" -f - < deploy/web_db_role.sql
  echo "  ✓ horyon_web role provisioned"
fi

# ── 7. Start remaining services ───────────────────────────────────────────────
echo
echo "=== Starting services ==="
docker compose $COMPOSE_FILES up -d bot monitor web

echo
echo "=== Waiting for healthy ==="
deadline=$(( $(date +%s) + 120 ))
containers=( horyon-bot horyon-monitor horyon-web )
while :; do
  all_ok=1
  for c in "${containers[@]}"; do
    h=$(docker inspect "$c" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo missing)
    printf '  %-20s %s\n' "$c" "$h"
    [ "$h" != "healthy" ] && [ "$h" != "none" ] && all_ok=0
  done
  [ "$all_ok" = 1 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && { echo "  WARN: some containers still starting — check logs."; break; }
  sleep 5
  echo "  ---"
done

echo
if [ "$DEV" = true ]; then
  echo "┌─────────────────────────────────────────────────────────────┐"
  echo "│  Dev stack running (polling mode, no Caddy/TLS)             │"
  echo "│                                                             │"
  echo "│  Web viewer  →  http://localhost:3000                       │"
  echo "│  Monitor     →  http://localhost:8090                       │"
  echo "│  Bot         →  polling (no webhook URL needed)             │"
  echo "│                                                             │"
  echo "│  Logs:   docker compose logs -f bot                         │"
  echo "│  Ingest: docker exec horyon-bot python -m app.ingest        │"
  echo "│  Digest: docker exec horyon-bot python -m app.digest        │"
  echo "└─────────────────────────────────────────────────────────────┘"
else
  echo "┌─────────────────────────────────────────────────────────────┐"
  echo "│  Production stack started.                                  │"
  echo "│  Add HORYON_MONITOR_HASH + HORYON_THREADS_HASH to .env,     │"
  echo "│  then: docker compose up -d --force-recreate caddy          │"
  echo "└─────────────────────────────────────────────────────────────┘"
fi
