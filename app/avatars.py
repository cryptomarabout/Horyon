"""Mirror entity avatars into Postgres for the Entity Map.

The map used to resolve every node's avatar in the BROWSER — logo_url (DeFiLlama/CoinGecko
CDN), else twitter_handle → unavatar.io — in a single burst on page load. unavatar.io
rate-limits that stampede, so many nodes silently dropped to a bare monogram (while clicking
one node, a lone request, succeeded). The web container is also forbidden from making any
per-request external call.

So the bot — the one place allowed to reach the internet — fetches each map entity's avatar
ONCE here, stores the bytes in ``entity_avatars``, and the public ``/api/avatar/[slug]`` route
serves them from our own DB. No client stampede, no web egress. The original logo_url /
unavatar URLs stay as a client-side fallback (see web/lib/entityGraph.js).

Mirrors app/og_cache.py shape: a build/refresh function + a small CLI.

  python -m app.avatars                 # refresh stale/missing map-entity avatars
  python -m app.avatars --force         # refetch all (avatars rotate occasionally)
  python -m app.avatars --slug aave     # one entity
  python -m app.avatars --no-persist    # fetch + report, no DB write
"""
from __future__ import annotations

import argparse
import logging
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote

from . import db

log = logging.getLogger(__name__)

_TIMEOUT = 15
_MAX_BYTES = 3 * 1024 * 1024          # 3 MB — avatars are tiny; reject anything absurd
_REFRESH_AFTER_DAYS = 30              # re-fetch avatars older than this on a plain run
# A browser-ish UA: unavatar.io and some CDNs 403 the default urllib agent.
_UA = "Mozilla/5.0 (compatible; horyon-avatar-mirror/1.0)"
# unavatar.io is a free service with a per-IP rate limit. We resolve hundreds of handles
# per run, so PACE the unavatar calls and back off on 429 — otherwise the whole burst trips
# the limit and most handles fall through to a monogram. This is a background cron, so slow
# is fine. CDN logo fetches (CoinGecko/DeFiLlama) aren't throttled.
_UNAVATAR_THROTTLE_SEC = 3.0
_RATE_LIMIT_BACKOFF = [20, 45, 90]    # seconds to wait on successive transient 429s
_MAX_BACKOFF_SEC = 120                # a Retry-After above this = a hard IP block (unavatar
                                      # hands out ~24h blocks): never sleep that long — abort
                                      # the run's handle fetches and let the next cron resume.
_MAX_UNAVATAR_PER_RUN = 40           # nibble, don't binge: stay well under unavatar's limit
                                      # so we never re-trigger a multi-hour block. The query
                                      # orders by mention_count, so each run fills top gaps first.


class _RateLimitedHard(Exception):
    """unavatar handed back a Retry-After too long to wait on — treat as an IP block."""


def _candidate_urls(logo_url: "str | None", twitter_handle: "str | None") -> list[str]:
    """Same cascade the client used: real logo first, then the X avatar via unavatar."""
    out: list[str] = []
    if logo_url:
        out.append(logo_url)
    h = (twitter_handle or "").lstrip("@").strip()
    if h:
        out.append(f"https://unavatar.io/twitter/{quote(h)}?fallback=false")
    return out


def _fetch_image(url: str) -> "tuple[bytes, str, str | None] | None":
    """Fetch one URL → (bytes, mime, etag) if it's a real, non-empty image; else None.

    Retries on HTTP 429 (unavatar.io rate-limit) with a back-off, honouring Retry-After."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    for attempt in range(len(_RATE_LIMIT_BACKOFF) + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    return None
                mime = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
                if not mime.startswith("image/"):
                    return None
                data = resp.read(_MAX_BYTES + 1)
                if not data or len(data) > _MAX_BYTES:
                    return None
                etag = resp.headers.get("ETag")
                return data, mime or "image/png", etag
        except HTTPError as exc:
            if exc.code == 429:
                ra = exc.headers.get("Retry-After")
                wait = int(ra) if (ra and ra.isdigit()) else _RATE_LIMIT_BACKOFF[attempt]
                if wait > _MAX_BACKOFF_SEC:
                    raise _RateLimitedHard(wait) from exc   # IP-blocked — bail, don't sleep
                if attempt < len(_RATE_LIMIT_BACKOFF):
                    log.info("avatars: 429 on %s — backing off %ss", url, wait)
                    time.sleep(wait)
                    continue
            log.debug("avatar fetch failed for %s: %s", url, exc)
            return None
        except Exception as exc:
            log.debug("avatar fetch failed for %s: %s", url, exc)
            return None
    return None


def _targets(force: bool, slug: "str | None") -> list[tuple[str, "str | None", "str | None"]]:
    """Map-relevant entities (those with at least one edge) that have an avatar source.

    Returns (slug, logo_url, twitter_handle). On a plain run, skips entities whose mirror is
    still fresh; ``force`` re-fetches everything. A single ``slug`` overrides the edge filter."""
    with db._conn() as conn, conn.cursor() as cur:
        if slug:
            cur.execute(
                "SELECT slug, logo_url, twitter_handle FROM entity_memory WHERE slug = %s",
                (slug,),
            )
            return [(r[0], r[1], r[2]) for r in cur.fetchall()]

        freshness = "" if force else (
            "AND NOT EXISTS (SELECT 1 FROM entity_avatars a "
            "WHERE a.slug = e.slug AND a.fetched_at > now() - interval '%d days')"
            % _REFRESH_AFTER_DAYS
        )
        cur.execute(
            f"""
            SELECT e.slug, e.logo_url, e.twitter_handle
            FROM entity_memory e
            WHERE (e.logo_url IS NOT NULL OR e.twitter_handle IS NOT NULL)
              AND EXISTS (
                SELECT 1 FROM entity_edges ed
                WHERE (ed.slug_a = e.slug OR ed.slug_b = e.slug) AND ed.weight >= 2
              )
              {freshness}
            ORDER BY e.mention_count DESC NULLS LAST
            """
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def refresh_avatars(force: bool = False, slug: "str | None" = None,
                    persist: bool = True, limit: "int | None" = None) -> dict:
    """Fetch + mirror avatars for map entities. Best-effort per entity — a failed fetch is
    logged and skipped (the client still has the original URL as a fallback)."""
    targets = _targets(force, slug)
    if limit:
        targets = targets[:limit]

    stored = 0
    missing = 0
    unavatar_used = 0
    unavatar_blocked = False
    for s, logo_url, handle in targets:
        got = None
        src = None
        for url in _candidate_urls(logo_url, handle):
            is_unavatar = "unavatar.io" in url
            if is_unavatar:
                # Don't touch unavatar once blocked, or past this run's nibble budget —
                # the entity stays "missing" and a later cron retries it.
                if unavatar_blocked or unavatar_used >= _MAX_UNAVATAR_PER_RUN:
                    continue
                unavatar_used += 1
                time.sleep(_UNAVATAR_THROTTLE_SEC)  # stay under unavatar's per-IP limit
            try:
                got = _fetch_image(url)
            except _RateLimitedHard as rl:
                unavatar_blocked = True
                log.warning("avatars: unavatar IP-blocked (retry-after %ss) — skipping "
                            "remaining handle fetches this run", rl)
                continue
            if got:
                src = url
                break
        if not got:
            missing += 1
            log.info("avatars: no image for %s", s)
            continue
        data, mime, etag = got
        if persist:
            db.upsert_entity_avatar(s, data, source_url=src, mime=mime, etag=etag)
        stored += 1
        log.info("avatars: stored %s (%d bytes, %s) from %s", s, len(data), mime, src)

    res = {"considered": len(targets), "stored": stored, "missing": missing,
           "unavatar_used": unavatar_used, "unavatar_blocked": unavatar_blocked,
           "persisted": persist}
    log.info("avatars: %s", res)
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Mirror entity avatars into Postgres for the map.")
    ap.add_argument("--force", action="store_true", help="refetch all, ignoring freshness")
    ap.add_argument("--slug", help="mirror a single entity by slug")
    ap.add_argument("--no-persist", action="store_true", help="fetch + report only; no DB write")
    ap.add_argument("--limit", type=int, help="cap how many entities to process")
    args = ap.parse_args()

    out = refresh_avatars(force=args.force, slug=args.slug,
                          persist=not args.no_persist, limit=args.limit)
    print(out)
