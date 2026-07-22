"""Intraday digest updates — lightweight INCREMENTAL briefs at fixed UTC slots (default
13:00 + 19:00, config.INTRADAY_SLOTS), on top of the 07:00 daily digest.

Each run summarizes ONLY feed items published since the last digest/update that day, applies
the same outward-LLM guards as the daily digest, appends a ``digest_updates`` row (rendered by
the web Daily Briefs as an "Intraday updates" timeline), and returns the body so the caller
(app/main.py) can send a short Telegram message. Deliberately does NOT run the heavy
post-digest pipeline (narratives rebuild / audio / threads / OG) — this is a top-up, not the
full digest.

CLI:  python -m app.intraday [--no-persist | --dry-run]
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from . import config, db, llm, prompts
from .digest import (
    _build_dedup_context, _count_bullets, _format_items, _keep_bullets_only,
    _post_filter_duplicates, _strip_foreign_hrefs,
)
from .telegram_html import sanitize

log = logging.getLogger(__name__)


def _window_and_dedup(day, now: datetime):
    """Return (since_ts, covered_urls, covered_bullets) for today's next update.

    since_ts = the most recent morning-digest / prior-update created_at, floored to
    now - INTRADAY_WINDOW_MAX_HOURS so a long gap can't balloon the item set into a full
    re-digest. covered_* are parsed from today's morning digest + earlier updates for the
    dedup pre-filter and the ALREADY-COVERED prompt block.
    """
    floor = now - timedelta(hours=config.INTRADAY_WINDOW_MAX_HOURS)
    last = db.get_last_covered_ts(day)
    since = last if last is not None else floor
    if since < floor:
        since = floor

    rows: list[tuple] = []
    morning = db.get_digest(day)
    if morning and morning.get("content"):
        rows.append((day, morning["content"]))
    for upd in db.get_digest_updates(day):
        rows.append((day, upd["content"]))
    covered_urls, covered_bullets = _build_dedup_context(rows)
    return since, covered_urls, covered_bullets


def build_intraday_update(now: datetime | None = None, *, persist: bool = True) -> dict | None:
    """Build (and optionally persist) one intraday update. Returns a dict
    {body, html, model, item_count, since, seq} or None when there is too little new to say.

    Never sends Telegram — that is the caller's job (it needs the bot handle). Best-effort:
    raises on an LLM/DB failure so the cron in main.py logs it; nothing is persisted on skip.
    """
    now = now or datetime.now(timezone.utc)
    day = now.date()

    since, covered_urls, covered_bullets = _window_and_dedup(day, now)
    rows = db.get_feed_items_since_ts(since, config.INTRADAY_ITEM_LIMIT)
    # Drop items whose exact URL was already cited today (morning digest or an earlier update).
    rows = [r for r in rows if not r.get("link") or r["link"] not in covered_urls]
    new_count = len(rows)
    if new_count < config.INTRADAY_MIN_ITEMS:
        log.info("intraday: %d new item(s) since %s (< min %d) — skipping",
                 new_count, since.isoformat(), config.INTRADAY_MIN_ITEMS)
        return None

    tweets = _format_items(rows)
    if not tweets.strip():
        log.info("intraday: no substantive item text after formatting — skipping")
        return None

    user = prompts.build_intraday_user(tweets, covered_bullets=covered_bullets)
    # Anti-injection allowlist: the input items' own LINK fields. Any output <a href> outside
    # this set is a planted/invented URL and is unwrapped to plain text (same rail as the digest).
    allowed_links = {r["link"] for r in rows if r.get("link")}

    raw, model = llm.complete(prompts.INTRADAY_UPDATE_SYSTEM, user, temperature=0.5)
    body = _keep_bullets_only(sanitize(raw))          # leak guard + em-dash de-slop
    body = _strip_foreign_hrefs(body, allowed_links)  # foreign-href guard
    body, n_removed = _post_filter_duplicates(body, covered_bullets)
    if n_removed:
        log.info("intraday: post-filter removed %d duplicate bullet(s)", n_removed)

    if _count_bullets(body) < 1:
        log.info("intraday: no parseable bullets after guards — skipping")
        return None

    seq = len(db.get_digest_updates(day)) + 1
    if persist:
        db.insert_digest_update(
            day, seq, body, model_used=model,
            window_start=since, item_count=new_count, trigger="cron",
        )
        log.info("intraday: stored update #%d for %s (%d new items, model=%s)",
                 seq, day.isoformat(), new_count, model)

    html = f"🕐 <b>Intraday Update</b>\n\n{body}"
    return {"body": body, "html": html, "model": model,
            "item_count": new_count, "since": since, "seq": seq}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Build an intraday digest update.")
    ap.add_argument("--no-persist", action="store_true", help="build but do not write the row")
    ap.add_argument("--dry-run", action="store_true", help="alias for --no-persist")
    args = ap.parse_args()
    persist = not (args.no_persist or args.dry_run)

    res = build_intraday_update(persist=persist)
    if res is None:
        print("(no intraday update — too few new items or nothing new to report)")
    else:
        print(res["html"])
        print(f"\n[model={res['model']}  new_items={res['item_count']}  "
              f"seq={res['seq']}  persist={persist}]")
