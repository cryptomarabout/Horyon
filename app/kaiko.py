"""Kaiko Research ingestion — sitemap-driven scraper for kaiko.com editorial content.

Kaiko (https://www.kaiko.com/resources) publishes crypto market-structure research,
"Perspectives", and data-blog analysis — high-signal institutional content — but exposes
NO RSS feed or public REST API: the WordPress backend is headless behind a Nuxt frontend,
so /feed/, /wp-json/, and ?feed=rss2 all 404. The resources listing itself is a paginated,
client-rendered page that is brittle to scrape.

What Kaiko DOES expose, on the public site, is:
  * Yoast XML sitemaps per post type — /new-sitemap.xml (/news/…), /resource-sitemap.xml
    (/resources/…), /report-sitemap.xml (/reports/…) — a stable, structured list of every
    article URL; and
  * a JSON-LD <script> block + OpenGraph meta on every server-rendered article page,
    carrying the headline, summary, publish date, and breadcrumb category.

So we DISCOVER article URLs from the sitemaps and, for each URL not already stored, fetch
the page once and pull (title, summary, datePublished, category) — the same (title, summary)
shape the RSS news feeds produce. A breadcrumb-category gate drops clearly non-editorial
collateral (client stories, product/use-case/methodology pages); editorial categories
(Data Blog, Perspectives, Macro, DeFi, Reports, News, …) are kept by default.

Items are routed through the shared ingest pipeline (clean_items → sanitize_items →
insert_feed_items → embed_missing → entity extraction), so they land in feed_items as
source_type='news' / creator='Kaiko' and flow into the daily digest, narrative clustering,
the entity graph, and search with zero extra wiring — exactly like any other news source.

Stdlib-only HTTP (urllib) + regex/json parsing — no new dependency (mirrors app/podcasts.py).
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from html import unescape
from urllib.parse import urlparse

from . import config, db, entities, ingest

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; Horyon/1.0; +https://horyon.xyz)"

# Yoast post-type sitemaps holding editorial content. Taxonomy sitemaps
# (resource_category, *_tag, *_author) and the page sitemap are intentionally excluded.
_SITEMAPS = (
    "https://www.kaiko.com/new-sitemap.xml",       # /news/…     — announcements + commentary
    "https://www.kaiko.com/resource-sitemap.xml",  # /resources/… — data-blog, perspectives, macro
    "https://www.kaiko.com/report-sitemap.xml",    # /reports/…   — research reports
)

# Bare listing/index pages that appear in the sitemaps but are not articles.
_INDEX_PATHS = {"/news", "/reports", "/resources"}

# URL fragments that are never editorial signal (marketing collateral / reference specs).
# "-methodology" catches the report-sitemap methodology docs, which carry no breadcrumb
# category so the category gate below can't see them (uniswap-v3-methodology, etc.).
_URL_DENY = ("client-story", "client-stories", "-methodology")

# Breadcrumb categories that are marketing / product / legal, not market intelligence.
# Matched (lowercased) against ALL breadcrumb names; anything NOT listed is kept, so new
# editorial categories don't need a code change. Keep this tight to avoid over-filtering.
_NOISE_CATEGORIES = {
    "client stories", "use cases", "guides", "methodologies",
    "indices factsheets", "indices rulebooks", "indices whitepapers",
    "indices vinter rulebooks", "indices legal regulatory",
}

# Generic breadcrumb endpoints stripped from the per-item topical tag list.
_GENERIC_CRUMBS = {"resources", "news", "reports", "home"}

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_LDJSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
_OG_RE = re.compile(r'<meta[^>]+property="og:(title|description)"[^>]+content="([^"]*)"', re.I)
_TITLE_SUFFIX_RE = re.compile(r"\s*[-|–—]\s*Kaiko\s*$", re.I)
_ARTICLE_TYPES = {"WebPage", "Article", "BlogPosting", "NewsArticle", "Report"}
_TAG_RE = re.compile(r"<[^>]+>")
_PARA_RE = re.compile(r"<p(?:\s[^>]*)?>(.+?)</p>", re.S | re.I)


def _unescape(s) -> str:
    """Fully decode HTML entities. Yoast double-encodes breadcrumb names
    (`&amp;#8217;` → `&#8217;` → `'`), so one unescape pass isn't enough."""
    s = str(s or "")
    for _ in range(3):
        t = unescape(s)
        if t == s:
            break
        s = t
    return s.strip()


# --------------------------------------------------------------------------- #
# HTTP (stdlib only)
# --------------------------------------------------------------------------- #
def _get(url: str, timeout: int = 25) -> str | None:
    """Fetch one URL as text. Never raises — returns None on any transport error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, "ignore")
    except Exception as exc:
        log.warning("kaiko fetch failed: %s (%s)", url, str(exc)[:160])
        return None


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def _sitemap_locs(sitemap_url: str) -> list[str]:
    xml = _get(sitemap_url)
    return [unescape(m) for m in _LOC_RE.findall(xml)] if xml else []


def discover_urls() -> list[str]:
    """All article URLs across the editorial sitemaps (normalised, deduped, index/marketing dropped)."""
    seen: set[str] = set()
    out: list[str] = []
    for sm in _SITEMAPS:
        for loc in _sitemap_locs(sm):
            url = ingest._normalize_url(loc)
            path = urlparse(url).path.rstrip("/")
            if path in _INDEX_PATHS or "/categories/" in path:
                continue
            if any(d in url.lower() for d in _URL_DENY):
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
    return out


# --------------------------------------------------------------------------- #
# Per-article extraction
# --------------------------------------------------------------------------- #
def _ldjson(html: str) -> dict:
    """Pull {name, description, articleBody, datePublished, dateModified, categories} from JSON-LD @graph."""
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(block)
        except (ValueError, TypeError):
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        nodes = graph if isinstance(graph, list) else ([data] if isinstance(data, dict) else [])
        page: dict = {}
        cats: list[str] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            t = n.get("@type")
            types = t if isinstance(t, list) else [t]
            if not page and any(x in _ARTICLE_TYPES for x in types):
                page = n
            if "BreadcrumbList" in types:
                cats = [_unescape(i.get("name", ""))
                        for i in n.get("itemListElement", []) if i.get("name")]
        if page:
            return {
                "name": _unescape(page.get("name") or page.get("headline") or ""),
                "description": _unescape(page.get("description") or ""),
                "articleBody": _unescape(page.get("articleBody") or ""),
                "datePublished": str(page.get("datePublished") or "").strip(),
                "dateModified": str(page.get("dateModified") or "").strip(),
                "categories": cats,
            }
    return {}


_MAIN_RE = re.compile(r"<main(?:\s[^>]*)?>(.+?)</main>", re.S | re.I)


def _extract_body(html: str, max_chars: int = 1200) -> str:
    """Extract article body text from rendered HTML paragraph tags.

    Kaiko uses headless WP + Nuxt SSR — body is in the DOM inside <main>.
    Scoping to <main> avoids sidebar / related-article bleed. Falls back to
    full-page scan if no <main> found. Skips nav/footer noise (very short
    paragraphs, social-share prompts, the article's own title echoed as a <p>).
    """
    main_m = _MAIN_RE.search(html)
    scope = main_m.group(1) if main_m else html

    chunks: list[str] = []
    total = 0
    for m in _PARA_RE.finditer(scope):
        text = _unescape(_TAG_RE.sub("", m.group(1))).strip()
        # Skip short fragments (nav, CTA) and bare article-title echoes (no verb, <70 chars)
        if len(text) < 60 or text.lower().startswith(("share", "follow", "subscribe", "©", "cookie")):
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        chunk = text[:remaining]
        chunks.append(chunk)
        total += len(chunk)
    return " ".join(chunks).strip()


def _og(html: str) -> dict:
    out: dict = {}
    for prop, content in _OG_RE.findall(html):
        out.setdefault(prop.lower(), _unescape(content))
    return out


def build_item(url: str, html: str) -> dict | None:
    """Shape one article page into a raw feed item (as ingest.clean_items expects),
    or None if it is non-editorial / lacks usable text."""
    ld = _ldjson(html)
    og = _og(html)

    cats = ld.get("categories") or []
    if any(c.strip().lower() in _NOISE_CATEGORIES for c in cats):
        log.debug("kaiko: skip non-editorial %s %s", url, cats)
        return None

    title = _TITLE_SUFFIX_RE.sub("", ld.get("name") or og.get("title") or "").strip()
    summary = og.get("description") or ld.get("description") or ""
    if not title:
        log.debug("kaiko: no title for %s", url)
        return None

    # Build rich content: title + OG/LD summary + JSON-LD articleBody + HTML body paragraphs.
    # Priority: articleBody from LD (clean, structured) > HTML paragraph extraction.
    article_body = ld.get("articleBody") or ""
    if not article_body:
        article_body = _extract_body(html)

    parts = [f"{title}."]
    if summary and summary.lower() not in title.lower():
        parts.append(summary)
    if article_body:
        # Avoid duplicating the summary if articleBody starts with it
        body_snippet = article_body[:1200]
        if summary and body_snippet.startswith(summary[:40]):
            body_snippet = body_snippet[len(summary):].lstrip(". ")
        if body_snippet:
            parts.append(body_snippet)
    content = " ".join(parts).strip()
    if len(ingest._plain(content)) < ingest.MIN_TEXT_LEN:
        log.debug("kaiko: thin content for %s", url)
        return None

    # Breadcrumb shape is [section, category…, page-title]; the real tags are the
    # middle slice (drop the generic section crumb and the trailing title crumb).
    tags = [c for c in cats[1:-1] if c.strip().lower() not in _GENERIC_CRUMBS]
    return {
        "link": url,
        "title": title,
        "content": content,
        "creator": "Kaiko",
        "pub_date": ld.get("datePublished") or ld.get("dateModified") or None,
        "categories": tags,
    }


# --------------------------------------------------------------------------- #
# Fetch + run
# --------------------------------------------------------------------------- #
# Non-editorial URLs classified this process — avoids re-fetching them every cron tick
# (they never enter feed_items, so existing_links() can't filter them). Mirrors the
# in-process channel-id cache in app/podcasts.py.
_skip_cache: set[str] = set()


def fetch_items(urls: list[str], known_links: set[str] | None = None,
                limit: int | None = None) -> list[dict]:
    """Fetch + shape the new (unknown) article URLs, bounded by ``limit``."""
    known = known_links or set()
    cap = limit if limit is not None else config.KAIKO_MAX_FETCH_PER_RUN
    candidates = [u for u in urls if u not in known and u not in _skip_cache]
    items: list[dict] = []
    fetched = 0
    for url in candidates:
        if cap and fetched >= cap:
            log.info("kaiko: hit per-run fetch cap (%d); %d candidates remain",
                     cap, len(candidates) - fetched)
            break
        html = _get(url)
        fetched += 1
        if config.KAIKO_FETCH_DELAY_SEC:
            time.sleep(config.KAIKO_FETCH_DELAY_SEC)
        if not html:
            continue
        item = build_item(url, html)
        if item is None:
            _skip_cache.add(url)
            continue
        items.append(item)
    log.info("kaiko: fetched %d page(s), %d editorial item(s)", fetched, len(items))
    return items


def run(persist: bool = True, limit: int | None = None) -> dict:
    """Discover → fetch new editorial articles → clean/insert/embed/extract. Never raises fatally."""
    if not config.KAIKO_ENABLED:
        log.info("kaiko: KAIKO_ENABLED is off; skipping")
        return {"enabled": False}

    urls = discover_urls()
    known = db.existing_links(urls)
    raw = fetch_items(urls, known_links=known, limit=limit)
    cleaned = ingest.clean_items(raw)
    final = ingest.sanitize_items(cleaned)

    if not persist:
        stats = {"discovered": len(urls), "known": len(known), "items": len(final)}
        log.info("kaiko dry-run: %s", stats)
        return stats

    inserted, new_links = db.insert_feed_items(final) if final else (0, set())
    embedded = db.embed_missing() if inserted else 0
    if new_links:
        new_items = [it for it in final if it["link"] in new_links]
        try:
            entities.extract_and_upsert_entities(new_items)
        except Exception:
            log.warning("kaiko entity extraction failed (non-fatal)", exc_info=True)

    stats = {"discovered": len(urls), "known": len(known), "items": len(final),
             "inserted": inserted, "embedded": embedded}
    log.info("kaiko run: %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Kaiko Research sitemap ingestion")
    ap.add_argument("--no-persist", action="store_true", help="fetch + shape only, no DB write")
    ap.add_argument("--limit", type=int, default=None,
                    help="max NEW article pages to fetch this run (default: KAIKO_MAX_FETCH_PER_RUN)")
    ap.add_argument("--backfill", action="store_true",
                    help="lift the per-run fetch cap to seed the full archive in one detached run")
    args = ap.parse_args()

    run_limit = args.limit
    if args.backfill and run_limit is None:
        run_limit = 100000  # effectively uncapped; already-stored URLs are still skipped
    print(run(persist=not args.no_persist, limit=run_limit))
