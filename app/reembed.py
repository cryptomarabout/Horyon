"""One-off: re-embed rows whose embed_version is behind the current cleaning logic.

Runs in place (no search downtime) and is resumable — safe to stop/restart.
Usage: ``python -m app.reembed``
"""
from __future__ import annotations

import logging
import time

from . import db, embeddings

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    todo = db.count_stale_embeddings()
    log.info("re-embed starting: %d stale rows (target version %d)", todo, embeddings.EMBED_VERSION)
    t0 = time.monotonic()

    def progress(done: int, last_id: int) -> None:
        rate = done / (time.monotonic() - t0) * 60 if done else 0
        remaining = max(todo - done, 0)
        eta = remaining / rate if rate else 0
        log.info("re-embedded %d/%d (%.0f/min, ~%.0f min left, id<=%d)",
                 done, todo, rate, eta, last_id)

    done = db.reembed_stale(batch_size=100, on_batch=progress)
    log.info("re-embed done: %d rows in %.0f min; stale remaining: %d",
             done, (time.monotonic() - t0) / 60, db.count_stale_embeddings())


if __name__ == "__main__":
    main()
