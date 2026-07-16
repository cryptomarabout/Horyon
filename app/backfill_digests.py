"""Backfill: regenerate all existing digests using the intelligence layer.

Processes dates chronologically (oldest → newest) so each digest can benefit
from chain context and analyst notes accumulated by earlier regenerations.

Usage (inside the container):
  docker exec horyon-bot python3 -m app.backfill_digests
  docker exec horyon-bot python3 -m app.backfill_digests --dry-run
  docker exec horyon-bot python3 -m app.backfill_digests --from-date 2026-05-20

What it does:
  1. Seeds entity_memory from defillama_protocols (covers ~300 major protocols instantly).
  2. Runs one entity extraction pass over recent feed items to discover people/VCs/etc.
  3. For each date (oldest first):
       a. Fetches feed items ingested on that day.
       b. Builds entity context, digest chain, analyst notes (via existing db helpers).
       c. Generates a new digest with the full intelligence prompt.
       d. Replaces the old digest row(s) for that date.
       e. Runs analyst extraction to populate notes + update entity summaries.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timezone

from . import analyst, config, db, entities, llm, prompts
from .telegram_html import sanitize
# Shared with the live digest build (and app/backfill.py) — this module used to carry its own
# pre-quality_flag copies, which had silently drifted from the NOTE-annotating originals.
from .digest import _format_items

log = logging.getLogger(__name__)


def _delete_digest_rows(d: date) -> int:
    """Delete all digest rows for date d. Returns count deleted."""
    with db._conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM crypto_digest WHERE date = %s RETURNING 1", (d,))
        return len(cur.fetchall())


def _build_and_store_digest(
    d: date,
    feed_items: list[dict],
    covered_urls: set[str] | None = None,
    covered_bullets: list[dict] | None = None,
    dry_run: bool = False,
) -> tuple[str | None, set[str], list[dict]]:
    """Build a digest for a specific past date and store it.

    Returns (raw_text, new_cited_urls, new_bullet_titles).
    raw_text is None when there are no usable feed items.
    Does not write to crypto_cache (keeps the live cache pointing to today).
    """
    covered_urls    = covered_urls    or set()
    covered_bullets = covered_bullets or []

    # Pre-filter: exclude feed items whose URL was already cited in a prior digest
    before = len(feed_items)
    feed_items = [r for r in feed_items
                  if not r.get("link") or r["link"] not in covered_urls]
    if before != len(feed_items):
        log.info("  dedup pre-filter: %d → %d items (%d removed)", before, len(feed_items), before - len(feed_items))

    tweets = _format_items(feed_items)
    if not tweets:
        log.warning("  no usable feed items for %s — skipping", d)
        return None, set(), []

    # TVL context (current snapshot — best we have for historical dates)
    try:
        tvl_rows = db.get_latest_tvl()
        tvl_ctx = prompts.format_tvl_context(tvl_rows)
    except Exception:
        log.debug("  no TVL context", exc_info=True)
        tvl_ctx = ""

    # Entity context via alias-matching
    try:
        entity_ctx = entities.build_entity_context(feed_items)
    except Exception:
        log.debug("  no entity context", exc_info=True)
        entity_ctx = ""

    # Analyst notes — populated for recent dates as backfill progresses
    try:
        notes_ctx = analyst.format_analyst_notes()
    except Exception:
        notes_ctx = ""

    # Digest chain — background context (covered_bullets handles the hard filter)
    try:
        chain_ctx = analyst.format_digest_chain()
    except Exception:
        chain_ctx = ""

    user = prompts.build_digest_user(
        tweets,
        previous_analysis="",
        tvl_context=tvl_ctx,
        entity_context=entity_ctx,
        digest_chain=chain_ctx,
        analyst_notes=notes_ctx,
        covered_bullets=covered_bullets,
    )

    log.info("  calling LLM for %s (covered=%d urls, %d titles, entity_ctx=%s)",
             d, len(covered_urls), len(covered_bullets),
             "yes" if entity_ctx else "no")

    t0 = time.monotonic()
    raw, model = llm.complete(prompts.DIGEST_SYSTEM, user)
    duration_ms = int((time.monotonic() - t0) * 1000)
    # Keep only • bullet lines first (anti reasoning-leak, mirrors build_digest), then dedup.
    from .digest import _keep_bullets_only, _post_filter_duplicates
    body = _keep_bullets_only(sanitize(raw))
    body, n_removed = _post_filter_duplicates(body, covered_bullets)
    if n_removed:
        log.info("  post-filter removed %d duplicate bullet(s)", n_removed)
    if "•" not in body:
        log.warning("  backfill %s: 0 bullets after cleaning (reasoning leak / empty) — skipping", d)
        return raw, set(), []

    if dry_run:
        print(f"\n{'='*60}\n{d} (DRY RUN — model: {model})\n{'='*60}")
        print(body[:1000])
        return raw, set(), []

    deleted = _delete_digest_rows(d)
    log.info("  deleted %d old row(s) for %s", deleted, d)

    # Store the filtered body (not the raw LLM output) so future dedup context is clean
    md = f"# Crypto Digest {d.isoformat()}\n\n{body}\n"
    db.insert_digest(d, md, model_used=model, trigger="backfill", duration_ms=duration_ms)
    log.info("  inserted new digest for %s (model=%s, %d ms)", d, model, duration_ms)

    # Analyst extraction: build notes + update entity summaries
    try:
        analyst.extract_and_persist(raw, d=d, source="backfill")
    except Exception:
        log.warning("  analyst extraction failed for %s (non-fatal)", d, exc_info=True)

    # Per-bullet analyst views: delete stale rows then regenerate from current content
    try:
        from .digest import generate_and_store_bullet_analyses
        deleted = db.delete_bullet_analyses(d)
        n_analyses = generate_and_store_bullet_analyses(d, body)
        log.info("  bullet analyses: %d stale deleted, %d generated for %s",
                 deleted, n_analyses, d)
    except Exception:
        log.warning("  bullet analysis generation failed for %s (non-fatal)", d, exc_info=True)

    # Extract what this new digest cited, so future dates can exclude it
    # Use the filtered body (not md which has the markdown header)
    from .digest import _build_dedup_context
    new_urls, new_titles = _build_dedup_context([(d, body)])
    return raw, new_urls, new_titles


def run_backfill(from_date: date | None = None, dry_run: bool = False) -> None:
    # 1. Seed entity_memory from defillama_protocols
    log.info("Step 1: seeding entity_memory from defillama_protocols…")
    seeded = db.seed_entities_from_protocols()
    log.info("  %d protocols seeded into entity_memory", seeded)

    # 2. Entity extraction pass over recent feed items
    log.info("Step 2: entity extraction pass over recent feed items…")
    try:
        recent = db.get_recent_feed_items(hours=config.DIGEST_WINDOW_HOURS * 14, limit=200)
        n = entities.extract_and_upsert_entities(recent)
        log.info("  %d entities upserted from recent feed sample", n)
    except Exception:
        log.warning("  entity extraction pass failed (non-fatal)", exc_info=True)

    # 3. Get all existing digest dates, oldest first
    with db._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT date FROM crypto_digest "
            "WHERE error IS NULL ORDER BY date ASC"
        )
        dates = [row[0] for row in cur.fetchall()]

    if from_date:
        dates = [d for d in dates if d >= from_date]

    log.info("Step 3: regenerating %d digest(s) starting from %s",
             len(dates), dates[0] if dates else "—")

    # Use the same 7-day rolling window as the live system.
    # Each date sees only the last 7 days of already-regenerated digests,
    # not cumulative history — this prevents over-deduplication on sparse days.
    from .digest import _build_dedup_context

    ok = 0
    skipped = 0
    for d in dates:
        log.info("Processing %s…", d)
        feed_items = db.get_feed_items_for_date(d, limit=300)
        log.info("  %d feed items for %s", len(feed_items), d)

        # Fresh 7-day context from DB (same logic as live build_digest)
        try:
            dedup_rows = db.get_digest_contents_for_dedup(days=7, before_date=d)
            covered_urls, covered_bullets = _build_dedup_context(dedup_rows)
            log.info("  dedup context: %d urls, %d titles (7d window)",
                     len(covered_urls), len(covered_bullets))
        except Exception:
            log.warning("  dedup context failed (non-fatal)", exc_info=True)
            covered_urls, covered_bullets = set(), []

        try:
            result, _, _ = _build_and_store_digest(
                d, feed_items,
                covered_urls=covered_urls,
                covered_bullets=covered_bullets,
                dry_run=dry_run,
            )
        except Exception:
            # The LLM call (and the empty-guard) run BEFORE the delete, so a failed date
            # keeps its existing digest. Log and continue so one bad date never aborts the run.
            log.warning("  digest regen failed for %s (existing digest kept) — continuing", d, exc_info=True)
            skipped += 1
            continue
        if result:
            ok += 1
        else:
            skipped += 1
        # Brief pause between LLM calls to avoid rate-limiting
        if not dry_run:
            time.sleep(2)

    log.info("Backfill complete: %d regenerated, %d skipped", ok, skipped)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Generate but do not write to DB; print first 1000 chars of each digest")
    ap.add_argument("--from-date", metavar="YYYY-MM-DD",
                    help="Only regenerate digests on or after this date")
    args = ap.parse_args()

    from_date = None
    if args.from_date:
        from_date = date.fromisoformat(args.from_date)

    run_backfill(from_date=from_date, dry_run=args.dry_run)
