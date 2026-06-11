#!/usr/bin/env bash
# test_feeds.sh — health + recency probe for RSS/Atom feed sources. Pure
# "run and read": fetches each source over HTTP and prints HTTP status, item
# count, age of the newest item, a flag (OK / STALE / DEAD) and a sample title.
# No feedparser / .venv / Docker needed — just curl + GNU date.
#
# Usage:
#   scripts/test_feeds.sh                 # test every URL in app/feeds.py SOURCES
#   scripts/test_feeds.sh --new           # test only lines added vs HEAD (git diff)
#   scripts/test_feeds.sh <url|handle>... # test specific feeds (bare handle -> nitter)
#
# Env:
#   STALE_DAYS=60   newest item older than this many days -> STALE
#   TIMEOUT=25      per-request curl timeout (seconds)
#
# Exit code: number of DEAD sources (0 = all reachable), capped at 250.
set -uo pipefail
cd "$(dirname "$0")/.."

STALE_DAYS="${STALE_DAYS:-60}"
TIMEOUT="${TIMEOUT:-25}"
UA="Mozilla/5.0 (compatible; Horyon/1.0; +https://github.com)"
FEEDS_FILE="app/feeds.py"

# --- resolve the list of URLs to test -------------------------------------
urls=()
case "${1:-}" in
  --new)
    # URLs present in the working tree but not in HEAD (newly added lines).
    mapfile -t urls < <(git diff HEAD -- "$FEEDS_FILE" \
      | grep -E '^\+' | grep -oE 'https?://[^"]+' || true)
    [ "${#urls[@]}" -eq 0 ] && { echo "No newly-added feed URLs vs HEAD."; exit 0; } ;;
  "" )
    mapfile -t urls < <(grep -oE 'https?://[^"]+' "$FEEDS_FILE") ;;
  * )
    for a in "$@"; do
      if [[ "$a" =~ ^https?:// ]]; then urls+=("$a")
      else urls+=("https://nitter.net/${a}/rss"); fi
    done ;;
esac

# --- probe one feed -> echoes a TSV row -----------------------------------
probe() {
  local url="$1" label body code items latest_epoch age flag sample now
  # short label: nitter handle, else hostname
  if [[ "$url" == *nitter.net/* ]]; then
    label=$(sed -E 's#.*nitter.net/([^/]+)/.*#\1#' <<<"$url")
  else
    label=$(sed -E 's#https?://([^/]+)/.*#\1#' <<<"$url")
  fi

  body=$(curl -sS -m "$TIMEOUT" -A "$UA" -w $'\nHTTP:%{http_code}' "$url" 2>/dev/null)
  code=$(grep -oE 'HTTP:[0-9]+$' <<<"$body" | tail -1 | cut -d: -f2)
  body=$(sed 's/HTTP:[0-9]*$//' <<<"$body")

  # item count: max(<item>, <entry>) — handles RSS + Atom
  local n_item n_entry
  n_item=$(grep -c '<item' <<<"$body")
  n_entry=$(grep -c '<entry' <<<"$body")
  items=$(( n_item > n_entry ? n_item : n_entry ))

  # newest date: max epoch across all pubDate/updated/published tags
  now=$(date -u +%s)
  latest_epoch=0
  while IFS= read -r d; do
    [ -z "$d" ] && continue
    local e; e=$(date -u -d "$d" +%s 2>/dev/null) || continue
    (( e > latest_epoch )) && latest_epoch=$e
  done < <(grep -oE '<(pubDate|updated|published)>[^<]+' <<<"$body" | sed -E 's/<[^>]+>//')

  if [ "$latest_epoch" -gt 0 ]; then
    age=$(( (now - latest_epoch) / 86400 ))
  else
    age="-"
  fi

  # flag
  if [ -z "$code" ] || [ "$code" -ge 400 ] 2>/dev/null || [ "$items" -eq 0 ]; then
    flag="DEAD"
  elif [ "$age" != "-" ] && [ "$age" -gt "$STALE_DAYS" ]; then
    flag="STALE"
  else
    flag="OK"
  fi

  # sample: 2nd <title> (1st is the channel/feed title), CDATA + tags stripped
  sample=$(grep -oE '<title>[^<]*' <<<"$body" | sed -E 's/<title>//' \
           | sed -n '2p' | sed -E 's/<!\[CDATA\[//; s/\]\]>//' | cut -c1-46)

  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$label" "${code:-ERR}" "$items" "$age" "$flag" "$sample"
}

# --- run ------------------------------------------------------------------
printf '%-20s %-5s %-5s %-5s %-6s %s\n' SOURCE HTTP ITEMS AGEd FLAG SAMPLE
rows=$(for u in "${urls[@]}"; do probe "$u"; done)
# sort: DEAD first, then STALE, then OK; within group by age desc
printf '%s\n' "$rows" | sort -t$'\t' -k5,5 -k4,4nr | \
  awk -F'\t' '{printf "%-20s %-5s %-5s %-5s %-6s %s\n",$1,$2,$3,$4,$5,$6}'

dead=$(grep -c $'\tDEAD\t' <<<"$rows")
stale=$(grep -c $'\tSTALE\t' <<<"$rows")
ok=$(grep -c $'\tOK\t' <<<"$rows")
echo "---"
echo "total ${#urls[@]}  |  OK $ok  STALE $stale  DEAD $dead   (STALE_DAYS=$STALE_DAYS)"
[ "$dead" -gt 250 ] && dead=250
exit "$dead"
