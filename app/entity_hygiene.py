"""Nightly entity-hygiene self-heal — the automated replacement for running the
manual audit/cleanup loop every day.

Runs only the conservative, idempotent maintenance passes (each already exists as a
hand-run command; this just sequences them on a cron):

  1. strip junk + generic-word aliases        (app.audit hygiene classes — always safe)
  2. merge duplicate entities                 (entity_audit merge — same-type guard)
  3. strip piggyback aliases                  (entity_audit dealias — dominance-gated)
  4. purge zero-footprint junk entities       (entity_audit purge-collisions — deletes
     ONLY provably-junk rows and records entity_review verdict='block' so extraction
     can never re-create them; anything with a footprint is left for human review)

Prevention lives upstream (extraction corpus gate + matcher matchable_term gate +
upsert blocklist); this pass mops up what still slips through. Scheduled daily at
05:20 UTC in app/main.py — before the 07:00 digest, so the day's digest always runs
against a clean entity table. Failures are logged and never propagate.

Manual run:  docker exec horyon-bot python3 -m app.entity_hygiene [--dry-run]
"""
from __future__ import annotations

import logging

from . import db

log = logging.getLogger(__name__)


def _strip_bad_aliases(quiet: bool = True) -> int:
    """Remove junk (punctuation/empty/1-char) + bare generic-word aliases — the same
    always-safe subset app.audit --fix applies (array_remove keeps everything else)."""
    from . import audit
    junk, generic, _gn, _ct, _vc = audit.check_entity_hygiene()
    n = 0
    with db._conn() as conn, conn.cursor() as cur:
        for slug, alias in junk + generic:
            cur.execute(
                "UPDATE entity_memory SET aliases = array_remove(aliases, %s), "
                "updated_at = now() WHERE slug = %s",
                (alias, slug),
            )
            n += cur.rowcount
    return n


def run(dry_run: bool = False) -> dict:
    """Run all self-heal passes; returns per-pass stats. Each pass is isolated —
    one failing never blocks the next."""
    from . import entity_audit

    stats: dict = {}

    try:
        stats["aliases_stripped"] = 0 if dry_run else _strip_bad_aliases()
    except Exception:
        log.exception("entity hygiene: alias strip failed")
        stats["aliases_stripped"] = "error"

    try:
        entity_audit.cmd_merge(dry_run=dry_run)
        stats["merge"] = "ok"
    except Exception:
        log.exception("entity hygiene: merge failed")
        stats["merge"] = "error"

    try:
        entity_audit.cmd_dealias(dry_run=dry_run)
        stats["dealias"] = "ok"
    except Exception:
        log.exception("entity hygiene: dealias failed")
        stats["dealias"] = "error"

    try:
        stats["purge"] = entity_audit.purge_collisions(dry_run=dry_run, quiet=True)
    except Exception:
        log.exception("entity hygiene: purge failed")
        stats["purge"] = "error"

    log.info("entity hygiene run complete: %s", stats)
    return stats


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Nightly entity-hygiene self-heal")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(run(dry_run=args.dry_run))
