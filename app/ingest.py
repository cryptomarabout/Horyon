"""Ingestion pipeline: fetch RSS → clean → sanitize → insert → embed."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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

# Boilerplate title patterns — RSS feeds that emit a section header or newsletter
# slug as the item title rather than the article headline.
_BOILERPLATE_TITLE_RE = re.compile(
    r"(newsletter\s*[#\-]\s*\d+|"        # "The Defiant Newsletter #214"
    r"^\w[\w\s]+\|[\w\s]+$|"              # "DeFi News | CryptoSlate"
    r"\bissue\s+#?\d+\b|"                 # "Issue #47"
    r"\bweek\s+in\s+|"                    # "Week in Ethereum"
    r"\bdaily\s+\w+\s+#?\d+\b)",          # "Daily Brief #123"
    re.IGNORECASE,
)

# UTM and tracking query params to strip for dedup normalisation.
_UTM_PARAM_RE = re.compile(r"(^|&)(utm_[^&=]+=?[^&]*|ref=[^&]*|source=[^&]*&?)", re.IGNORECASE)


def _normalize_url(url: str) -> str:
    """Normalise a URL for dedup: https scheme, strip UTM/ref params, strip trailing slash."""
    if not url:
        return url
    try:
        p = urlparse(url)
        # Normalise scheme to https (nitter serves http, canonical is https)
        scheme = "https" if p.scheme in ("http", "https") else p.scheme
        # Strip UTM + ref tracking params; preserve other params
        if p.query:
            qs = parse_qs(p.query, keep_blank_values=True)
            clean_qs = {k: v for k, v in qs.items()
                        if not k.lower().startswith(("utm_", "ref", "source"))}
            query = urlencode(clean_qs, doseq=True)
        else:
            query = ""
        path = p.path.rstrip("/") or "/"
        return urlunparse((scheme, p.netloc.lower(), path, p.params, query, ""))
    except Exception:
        return url


def title_content_coherence_check(title: str, content: str) -> str:
    """Classify the quality of a feed item based on cheap heuristics.

    Returns one of:
      'ok'                  — content is substantive and title is informative
      'thin_content'        — content body is too short to be useful
      'nitter_handle_title' — RSS title is a @handle or bare URL, not a topic signal
      'boilerplate_title'   — RSS title is a generic section header / newsletter slug

    Never raises. Pure Python — no LLM, no embedding.
    """
    try:
        plain = _WS_RE.sub(" ", _TAG_RE.sub(" ", content or "")).strip()
        if len(plain) < 80:
            return "thin_content"
        t = (title or "").strip()
        if t.startswith("@") or re.match(r"^https?://", t):
            return "nitter_handle_title"
        if _BOILERPLATE_TITLE_RE.search(t):
            return "boilerplate_title"
    except Exception:
        pass
    return "ok"


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
        "title": (e.get("title") or "").strip(),
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
    URL normalisation strips UTM/ref params before the link-dedup check so that
    the same article syndicated with different tracking suffixes is treated as one.
    """
    seen_links: set[str] = set()
    seen_text: set[str] = set()
    out: list[dict] = []
    for r in raws:
        raw_link = (r.get("link") or "").replace("https://nitter.net", "https://x.com")
        link = _normalize_url(raw_link)
        title = r.get("title") or ""
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

        quality_flag = title_content_coherence_check(title, content)
        out.append({
            "link": link,
            "title": title[:512] if title else "",
            "content": content,
            "creator": feed_name,
            "source_type": "twitter" if is_twitter else "news",
            "pub_date": r.get("pub_date"),
            "categories": r.get("categories") or [],
            "mentions": mentions,
            "quality_flag": quality_flag,
        })
    return out


def sanitize_items(cleaned: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    out = []
    for c in cleaned:
        pd = c.get("pub_date")
        if pd:
            # Cap feed-supplied dates that are in the future (scheduled posts, clock skew).
            # Also reject unparseable strings — fall through to now_iso below.
            try:
                pd_dt = datetime.fromisoformat(pd)
                # Make aware if naive (feedparser sometimes returns naive UTC)
                if pd_dt.tzinfo is None:
                    pd_dt = pd_dt.replace(tzinfo=timezone.utc)
                if pd_dt > now:
                    pd = now_iso
            except (ValueError, TypeError):
                pd = None
        out.append({
            "link": (c["link"] or "")[:2048],
            "title": (c.get("title") or "")[:512],
            "content": (c["content"] or "")[:10000],
            "creator": (c["creator"] or "")[:256],
            "source_type": c.get("source_type") or "twitter",
            "metadata": json.dumps({"categories": c.get("categories") or []}),
            "pub_date": pd or now_iso,
            "mentions": c.get("mentions") or [],
            "quality_flag": c.get("quality_flag") or "ok",
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

    # Escalate sources with chronic consecutive failures — may need URL update or removal.
    try:
        chronic = db.get_chronic_failing_sources(min_failures=5)
        if chronic:
            for url, failures in chronic:
                log.warning(
                    "ingest: chronic failure — %s has %d consecutive failures and may need "
                    "URL update or removal from feeds.py", url, failures,
                )
    except Exception:
        log.debug("ingest: could not check chronic failures", exc_info=True)
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
