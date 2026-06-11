#!/usr/bin/env bash
# delegate.sh — send a self-contained task to a cheap/free OpenRouter model and
# print only the model's text reply. Keeps Claude's orchestration overhead to a
# single synchronous call (no polling, no temp files).
#
# Usage:
#   scripts/delegate.sh "write a python slugify function"      # prompt as arg
#   echo "long spec..." | scripts/delegate.sh                  # prompt on stdin
#   MODEL=qwen/qwen-2.5-coder-32b-instruct scripts/delegate.sh "..."  # override model
#
# Env: reads OPENROUTER_API_KEY from .env (or the environment).
set -euo pipefail

cd "$(dirname "$0")/.."

# Load key from .env if not already exported.
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -f .env ]; then
  OPENROUTER_API_KEY=$(grep -hoE '^OPENROUTER_API_KEY=.+' .env | head -1 | cut -d= -f2-)
fi
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "delegate.sh: OPENROUTER_API_KEY not set (and not found in .env)" >&2
  exit 1
fi

MODEL="${MODEL:-poolside/laguna-m.1:free}"
BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"

# Prompt: first arg wins, else stdin.
if [ "$#" -gt 0 ]; then
  PROMPT="$*"
else
  PROMPT="$(cat)"
fi
if [ -z "${PROMPT// /}" ]; then
  echo "delegate.sh: empty prompt" >&2
  exit 1
fi

# Build the JSON body safely (jq escapes the prompt).
BODY=$(jq -n --arg model "$MODEL" --arg content "$PROMPT" \
  '{model: $model, messages: [{role: "user", content: $content}]}')

RESP=$(curl -sS --fail-with-body "$BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$BODY") || { echo "delegate.sh: request failed:" >&2; echo "$RESP" >&2; exit 1; }

# Print content, or surface an API error object.
echo "$RESP" | jq -r '
  if .choices then .choices[0].message.content
  elif .error then "delegate.sh: API error: \(.error.message // .error)" | halt_error(1)
  else "delegate.sh: unexpected response: \(.)" | halt_error(1)
  end'
