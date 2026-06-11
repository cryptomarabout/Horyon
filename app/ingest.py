"""Ingestion pipeline: fetch RSS → clean → sanitize → insert → embed."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser

from . import config, db, entities
from .feeds import FEED_NAMES, SOURCES

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; Horyon/1.0; +https://github.com)"
_STATUS_ID_RE = re.compile(r"/status/\d{10,}")
_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{1,50})")
MIN_TEXT_LEN = 40


def _plain(html: str) -> str:
    """Strip tags + collapse whitespace, for length/RT filtering."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()


def _entry_content(entry) -> str:
    if entry.get("summary"):
        return entry["summary"]
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        return content[0]["value"]
    return entry.get("title", "") or ""


def _entry_pub_iso(entry) -> str | None:
    pp = entry.get("published_parsed") or entry.get("updated_parsed")
    if pp:
        return datetime(*pp[:6], tzinfo=timezone.utc).isoformat()
    return None


def fetch_source(url: str) -> dict:
    """Fetch one feed. Returns an outcome dict; never raises.

    ``{url, ok, status, item_count, items, error}`` — ``ok`` is False on transport
    errors, HTTP >= 400, or an unparseable feed with no entries.
    """
    try:
        parsed = feedparser.parse(url, agent=_UA)
    except Exception as exc:
        log.warning("feed fetch failed: %s", url)
        return {"url": url, "ok": False, "status": None, "item_count": 0,
                "items": [], "error": str(exc)[:300]}

    status = parsed.get("status")
    entries = parsed.entries or []
    error = None
    if parsed.get("bozo") and not entries:
        exc = parsed.get("bozo_exception")
        error = str(exc)[:300] if exc else "unparseable feed"
    elif status is not None and status >= 400:
        error = f"HTTP {status}"

    items = [{
        "link": e.get("link") or "",
        "content": _entry_content(e),
        "creator": e.get("author") or e.get("dc_creator") or "",
        "pub_date": _entry_pub_iso(e),
        "categories": [t.get("term", "") for t in e.get("tags", []) if t.get("term")],
    } for e in entries]

    return {"url": url, "ok": error is None, "status": status,
            "item_count": len(entries), "items": items, "error": error}


def fetch_all() -> list[dict]:
    """Fetch every source concurrently; returns per-source outcome dicts."""
    with ThreadPoolExecutor(max_workers=10) as pool:
        return list(pool.map(fetch_source, SOURCES))


def clean_items(raws: list[dict]) -> list[dict]:
    """nitter→x rewrite, source_type detect, feed-name resolve, filter, dedupe.

    Length/RT filters run on the plain text (tags stripped) so an HTML wrapper
    can't sneak a trivial tweet past the minimum-length check. Dedupe is by link
    AND by plain-text content (collapses repeated trivial tweets within a batch).
    """
    seen_links: set[str] = set()
    seen_text: set[str] = set()
    out: list[dict] = []
    for r in raws:
        link = (r.get("link") or "").replace("https://nitter.net", "https://x.com")
        content = r.get("content") or ""
        creator = r.get("creator") or ""
        is_twitter = "x.com" in link
        text = _plain(content)

        if is_twitter:
            feed_name = creator
        else:
            feed_name = next(
                (name for domain, name in FEED_NAMES.items() if domain in link),
                creator,
            )

        is_fake = (
            not link
            or link.endswith("/status/1")
            or "example" in link
            or ("/status/" in link and not _STATUS_ID_RE.search(link))
        )
        if is_fake or text.startswith("RT") or len(text) < MIN_TEXT_LEN:
            continue
        if link in seen_links or text in seen_text:
            continue
        seen_links.add(link)
        seen_text.add(text)

        # Parse @mentions from plain text before anything is stripped.
        # Deduplicated, lowercased, prefixed with @. Capped at 20 to bound array size.
        raw_mentions = _MENTION_RE.findall(text)
        mentions = list(dict.fromkeys(f"@{m.lower()}" for m in raw_mentions))[:20]

        out.append({
            "link": link,
            "content": content,
            "creator": feed_name,
            "source_type": "twitter" if is_twitter else "news",
            "pub_date": r.get("pub_date"),
            "categories": r.get("categories") or [],
            "mentions": mentions,
        })
    return out


def sanitize_items(cleaned: list[dict]) -> list[dict]:
    now_iso = datetime.now(timezone.utc).isoformat()
    out = []
    for c in cleaned:
        out.append({
            "link": (c["link"] or "")[:2048],
            "content": (c["content"] or "")[:10000],
            "creator": (c["creator"] or "")[:256],
            "source_type": c.get("source_type") or "twitter",
            "metadata": json.dumps({"categories": c.get("categories") or []}),
            "pub_date": c.get("pub_date") or now_iso,
            "mentions": c.get("mentions") or [],
        })
    return out


def run_once(dry_run: bool = False) -> dict:
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    results = fetch_all()
    raws = [item for r in results for item in r["items"]]
    cleaned = clean_items(raws)
    final = sanitize_items(cleaned)
    sources_ok = sum(1 for r in results if r["ok"])
    sources_failed = len(results) - sources_ok
    if sources_failed:
        bad = ", ".join(f"{r['url'].split('/')[-2] or r['url']}({r['error']})"
                        for r in results if not r["ok"])
        log.warning("ingest: %d/%d sources failed: %s", sources_failed, len(results), bad)
    log.info("ingest: %d raw → %d cleaned (%d/%d sources ok)",
             len(raws), len(final), sources_ok, len(results))

    if dry_run:
        return {"raw": len(raws), "cleaned": len(final), "inserted": 0, "embedded": 0,
                "sources_ok": sources_ok, "sources_failed": sources_failed}

    inserted, new_links = db.insert_feed_items(final)
    embedded = db.embed_missing()
    duration_ms = int((time.monotonic() - t0) * 1000)
    db.record_ingest_run(started, len(raws), len(final), inserted, embedded,
                         sources_ok, sources_failed, duration_ms)
    db.update_source_health(results)
    log.info("ingest: %d candidates → %d new rows, embedded %d (%d ms)",
             len(final), inserted, embedded, duration_ms)

    # Entity extraction: only on items that were actually new this cycle.
    # Passing the full batch would re-analyse ~1400 already-known items every 20 min.
    if new_links:
        new_items = [it for it in final if it["link"] in new_links]
        try:
            entities.extract_and_upsert_entities(new_items)
        except Exception:
            log.warning("entity extraction failed (non-fatal)", exc_info=True)

    return {"raw": len(raws), "cleaned": len(final), "inserted": inserted,
            "embedded": embedded, "sources_ok": sources_ok, "sources_failed": sources_failed}


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(run_once(dry_run=args.dry_run))
