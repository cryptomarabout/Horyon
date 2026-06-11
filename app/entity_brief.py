"""Entity Intel Briefs: pre-compute per-entity updates post-digest.

Triggered once per day after the daily digest runs. For each entity that appears
(word-boundary match) in that day's bullets, generates a brief using:
  - Today's digest bullets mentioning the entity
  - Historical digest bullets (last 14 days)
  - Recent feed items (semantic search, last 14 days)

Stored in entity_intel_brief; served by specialized.py and /api/search as an
instant cache hit that bypasses the full ReAct loop.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_t

from . import db, llm, prompts

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'", "&nbsp;": " ",
}

# A reasoning fallback model (e.g. nemotron) emits its chain-of-thought ("We need to
# produce an intel brief…") before — or instead of — the brief. Strip <think> blocks and
# any preamble before the first 🔎 header / • bullet, then require ≥1 bullet, else reject
# (a cached reasoning-leak is worse than a cache miss → falls through to the live agent).
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def _decode(s: str) -> str:
    return re.sub(r"&[a-z#0-9]+;", lambda m: _HTML_ENTITIES.get(m.group(), m.group()), s)


def _clean_brief(content: str) -> str:
    """Keep only the brief itself: the 🔎 header + • bullet lines, dropping leaked reasoning."""
    if not content:
        return ""
    text = _THINK_OPEN_RE.sub("", _THINK_RE.sub("", content))
    kept, started = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if not started:
            if s.startswith("🔎") or s.startswith("•"):
                started = True
            else:
                continue  # skip preamble / planning lines
        kept.append(ln)
    return "\n".join(kept).strip()


def _parse_bullets(html: str) -> list[dict]:
    """Parse Telegram-HTML digest into [{title, body, link}]."""
    bullets = []
    for line in html.replace("\r", "").split("\n"):
        t = line.strip()
        if not t or not t.startswith("•"):
            continue
        body0 = t[1:].lstrip()
        title_m = re.search(r"<b>([\s\S]*?)</b>", body0, re.I)
        link_m  = re.search(r'<a[^>]*href="([^"]+)"', body0, re.I)
        rest = re.sub(r"<b>[\s\S]*?</b>", "", body0, flags=re.I)
        rest = re.sub(r"<a[^>]*>[\s\S]*?</a>", "", rest, flags=re.I)
        rest = re.sub(r"^\s*[—–-]+\s*", "", rest)
        title = _decode(re.sub(r"<[^>]+>", "", title_m.group(1) if title_m else "")).strip()
        body  = re.sub(r"\s+", " ", _decode(re.sub(r"<[^>]+>", "", rest))).strip()
        if title:
            bullets.append({"title": title, "body": body,
                             "link": link_m.group(1) if link_m else None})
    return bullets


def _find_entities_in_text(plain_text: str) -> list[dict]:
    """Return entity_memory rows (with mention_count >= 3) that appear word-boundary in text."""
    all_entities = db.get_entities_for_briefing(min_mentions=3)
    found: list[dict] = []
    seen: set[str] = set()
    for slug, name, type_, aliases in all_entities:
        if slug in seen:
            continue
        candidates = [name] + (aliases or [])
        for candidate in candidates:
            if not candidate or len(candidate) < 3:
                continue
            pattern = r"\b" + re.escape(candidate) + r"\b"
            if re.search(pattern, plain_text, re.IGNORECASE):
                found.append({"slug": slug, "name": name, "type": type_})
                seen.add(slug)
                break
    return found


def _get_historical_bullets_for_entity(entity_name: str, days: int = 14) -> list[dict]:
    """Return digest bullets from the last N days (excluding today) that mention entity_name."""
    rows = db.get_digest_contents_for_dedup(days=days)
    pattern = re.compile(r"\b" + re.escape(entity_name) + r"\b", re.IGNORECASE)
    matching: list[dict] = []
    for d, content in rows:
        if not content:
            continue
        for bullet in _parse_bullets(content):
            text = bullet.get("title", "") + " " + bullet.get("body", "")
            if pattern.search(text):
                matching.append({"date": str(d), **bullet})
    return matching


def _generate_brief(entity: dict, today_bullets: list[dict], digest_date: date_t) -> dict | None:
    """Generate brief HTML for one entity. Returns enriched entity dict or None on failure."""
    name = entity["name"]
    try:
        pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)

        # Filter today's bullets for this entity
        entity_today = [
            {"date": str(digest_date), **b}
            for b in today_bullets
            if pattern.search(b.get("title", "") + " " + b.get("body", ""))
        ]

        # Historical bullets (last 14 days, excluding today)
        hist_bullets = _get_historical_bullets_for_entity(name, days=14)

        all_bullets = entity_today + hist_bullets
        feed_items  = db.search_feed(name, topk=10, days=14)

        if not all_bullets and not feed_items:
            log.debug("entity brief: no data for %r, skipping", name)
            return None

        user_prompt = prompts.build_entity_brief_user(name, all_bullets, list(feed_items))
        # 900 tokens (was 600): a reasoning fallback model needs room to think AND still
        # emit the brief; with 600 it burned the budget reasoning and produced no bullets.
        content, model = llm.complete(prompts.ENTITY_BRIEF_SYSTEM, user_prompt, max_tokens=900, temperature=0.4)
        brief = _clean_brief(content)
        if "•" not in brief:  # no bullets survived → reasoning-only / empty: don't cache garbage
            log.warning("entity brief: no usable bullets for %r (model=%s, likely reasoning leak) — skipping",
                        name, model)
            return None
        return {"name": name, "brief_html": brief, "model_used": model}
    except Exception:
        log.warning("entity brief: generation failed for %r", name, exc_info=True)
        return None


def update_entity_briefs_from_digest(digest_date: date_t, bullets_html: str) -> int:
    """Post-digest entry point: generate/update briefs for all entities in today's digest.

    Runs up to 5 parallel LLM calls. Failures per-entity are logged and skipped.
    Returns count of successfully stored briefs.
    """
    plain_text   = _TAG_RE.sub(" ", bullets_html)
    entities     = _find_entities_in_text(plain_text)
    today_bullets = _parse_bullets(bullets_html)

    if not entities:
        log.info("entity briefs: no eligible entities found in digest %s", digest_date)
        return 0

    log.info("entity briefs: found %d entities in digest %s — generating", len(entities), digest_date)

    stored = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_generate_brief, e, today_bullets, digest_date): e for e in entities}
        for fut in as_completed(futures):
            e = futures[fut]
            try:
                result = fut.result()
                if result:
                    db.upsert_entity_intel_brief(
                        result["name"], result["brief_html"],
                        result["model_used"], digest_date,
                    )
                    stored += 1
            except Exception:
                log.warning("entity brief: DB upsert failed for %r", e.get("name"), exc_info=True)

    log.info("entity briefs: stored %d/%d briefs for %s", stored, len(entities), digest_date)
    return stored
