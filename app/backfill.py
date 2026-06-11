"""Backfill daily digests for past dates.

Usage:
    python -m app.backfill 2026-05-18 2026-05-19 ...
    python -m app.backfill --range 2026-05-18 2026-05-27   # inclusive
    python -m app.backfill --dry-run 2026-05-21             # print without inserting
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, timedelta

from . import config, db, llm, prompts
from .digest import _format_items
from .telegram_html import sanitize

log = logging.getLogger(__name__)


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def backfill_date(target: date, dry_run: bool = False) -> str:
    """Generate and (optionally) persist a digest for a specific past date."""
    log.info("backfilling %s", target)
    rows = db.get_feed_items_for_date(target, limit=config.DIGEST_LIMIT)
    if not rows:
        log.warning("no feed items found for %s — skipping", target)
        return ""

    log.info("  %d feed items for %s", len(rows), target)
    tweets = _format_items(rows)

    try:
        tvl_rows = db.get_latest_tvl()
    except Exception:
        tvl_rows = []
    tvl_ctx = prompts.format_tvl_context(tvl_rows)

    user = prompts.build_digest_user(tweets, "", tvl_context=tvl_ctx)

    t0 = time.monotonic()
    raw, model = llm.complete(prompts.DIGEST_SYSTEM, user)
    duration_ms = int((time.monotonic() - t0) * 1000)

    # Keep only • bullet lines (mirrors digest.build_digest) so a reasoning model's
    # chain-of-thought never gets persisted as the digest body.
    from .digest import _keep_bullets_only
    body = _keep_bullets_only(sanitize(raw))
    html = f"🧠 <b>Crypto Twitter Digest</b>\n\n{body}"

    if "•" not in body:
        log.warning("  backfill %s: 0 bullets after cleaning (reasoning leak?) — not persisting", target)
        return html

    if not dry_run:
        # Persist the cleaned body, never the raw LLM output.
        md = f"# Crypto Digest {target.isoformat()}\n\n{body}\n"
        db.insert_digest(target, md, model_used=model, trigger="backfill",
                         duration_ms=duration_ms)
        log.info("  persisted digest for %s (%s, %d ms)", target, model, duration_ms)
    else:
        log.info("  [dry-run] would persist for %s (%s, %d ms)", target, model, duration_ms)
        print(html)

    return html


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Backfill daily digests for past dates")
    ap.add_argument("dates", nargs="*", help="YYYY-MM-DD dates to backfill")
    ap.add_argument("--range", nargs=2, metavar=("START", "END"),
                    help="Inclusive date range to backfill")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print digest without writing to DB")
    args = ap.parse_args()

    targets: list[date] = []
    if args.range:
        start = date.fromisoformat(args.range[0])
        end = date.fromisoformat(args.range[1])
        targets = list(_date_range(start, end))
    for d in args.dates:
        targets.append(date.fromisoformat(d))

    if not targets:
        ap.error("provide at least one date or --range START END")

    targets.sort()
    log.info("backfilling %d date(s): %s … %s", len(targets), targets[0], targets[-1])

    for target in targets:
        try:
            backfill_date(target, dry_run=args.dry_run)
        except Exception:
            log.exception("failed to backfill %s", target)


if __name__ == "__main__":
    main()
