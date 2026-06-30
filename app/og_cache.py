"""Pre-render and cache the /api/og social card for a digest date.

The public web `/api/og` route used to render the card (satori / next/og) on EVERY
request — an unbounded compute cost on a public, unauthenticated site. Instead, the bot
renders the card exactly once per digest (here, as a post-digest step) by calling the web
container over the internal docker network, and stores the PNG in ``digest_og``. The public
route then serves those bytes directly (read-only) and only falls back to a live render on a
genuine cache miss.

"Cache the image after each digest update" → run as a post-digest step (and on any digest
regen): we DELETE the existing row first so the route renders fresh, fetch, then store.

Mirrors the app/briefing.py shape: a build function + a small CLI (--date/--no-persist/--backfill).
"""
from __future__ import annotations

import argparse
import logging
import urllib.request
from datetime import date as date_t, datetime, timezone

from . import config, db

log = logging.getLogger(__name__)

# The canonical share card: top-5 daily. The route clamps bullets to 1..5 anyway.
_OG_TYPE = "daily"
_OG_BULLETS = 5
_TIMEOUT = 30


def _render_via_web(d: date_t) -> tuple[bytes, str]:
    """Fetch a freshly rendered card from the web container. Caller must have cleared any
    cached row first so the route doesn't hand back the stale cached bytes."""
    url = f"{config.WEB_INTERNAL_URL}/api/og?date={d.isoformat()}&type={_OG_TYPE}&bullets={_OG_BULLETS}"
    req = urllib.request.Request(url, headers={"User-Agent": "horyon-og-warmer"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"og render returned HTTP {resp.status}")
        data = resp.read()
        mime = resp.headers.get("Content-Type", "image/png").split(";")[0].strip() or "image/png"
    if not data:
        raise RuntimeError("og render returned empty body")
    return data, mime


def build_og_for_date(d: date_t, persist: bool = True) -> "dict | None":
    """Render the OG card for a date and cache it in ``digest_og``. Returns a small summary
    dict, or None if rendering failed. Best-effort: callers (post-digest step) swallow errors."""
    if persist:
        # Clear first so the route renders fresh rather than echoing a stale cached row.
        db.delete_og_card(d)
    png, mime = _render_via_web(d)
    if persist:
        db.upsert_og_card(d, png, mime=mime, source_at=datetime.now(timezone.utc))
        log.info("og_cache: stored card for %s (%d bytes, %s)", d, len(png), mime)
    return {"digest_date": d, "byte_size": len(png), "mime": mime}


def _dates_with_digests(limit: int) -> list[date_t]:
    with db._conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT date FROM crypto_digest
               WHERE error IS NULL AND content IS NOT NULL AND content <> ''
               ORDER BY date DESC LIMIT %s""",
            (limit,),
        )
        return [r[0] for r in cur.fetchall()]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Pre-render and cache the /api/og card for a digest date.")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="build for one date (default: today)")
    ap.add_argument("--no-persist", action="store_true", help="render only; no DB write")
    ap.add_argument("--backfill", action="store_true", help="render cards for all recent digest dates")
    ap.add_argument("--limit", type=int, default=60, help="backfill: how many recent dates (default 60)")
    args = ap.parse_args()

    if args.backfill:
        for d in _dates_with_digests(args.limit):
            try:
                build_og_for_date(d, persist=not args.no_persist)
            except Exception as exc:
                log.warning("og_cache: failed for %s: %s", d, exc)
    else:
        d = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
             else datetime.now(timezone.utc).date())
        res = build_og_for_date(d, persist=not args.no_persist)
        print(res)
