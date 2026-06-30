"""Entity Intel Briefs: pre-compute per-entity updates post-digest.

Triggered once per day after the daily digest runs. For EVERY entity that appears
(word-boundary match) in that day's bullets — no mention-count floor — generates a
brief using:
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
    """Return every entity_memory row that appears word-boundary in text.

    No mention-count floor: the public web serves these briefs as the ONLY answer for
    an entity-tag click (the live-LLM fallback was removed), so coverage must equal the
    set of entities that can appear as a clickable tag — i.e. anything mentioned in a
    bullet, even on its first appearance.
    """
    all_entities = db.get_entities_for_briefing(min_mentions=1)
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


def _fmt_usd(v: float) -> str:
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _entity_db_facts(slug: str, name: str) -> str:
    """Live DeFiLlama TVL + Snapshot governance for one entity, as a labeled context block —
    the same verified grounding the per-bullet analyst gets, which the brief lacked. Returns
    an empty string when no DB facts exist. Best-effort: failures degrade to no block."""
    if not slug:
        return ""
    lines: list[str] = []
    try:
        prot = {p["slug"]: p for p in db.get_protocols_by_slugs([slug])}.get(slug)
        if prot and prot.get("tvl_usd") is not None:
            chg = prot.get("tvl_change_7d")
            chg_str = f" ({chg:+.1f}% 7d)" if chg is not None else ""
            cat = f" · {prot['category']}" if prot.get("category") else ""
            lines.append(f" - {name}: TVL {_fmt_usd(float(prot['tvl_usd']))}{chg_str}{cat}")
    except Exception:
        log.debug("entity brief: TVL fetch failed for %r", name, exc_info=True)
    try:
        props = db.get_governance_for_entity(slug, name)
        if props:
            titles = ", ".join(f"'{p['title']}' ({p['state']})" for p in props)
            lines.append(f" - Recent governance: {titles}")
    except Exception:
        log.debug("entity brief: governance fetch failed for %r", name, exc_info=True)
    if not lines:
        return ""
    return (
        "VERIFIED DATABASE FACTS (live DeFiLlama TVL + Snapshot governance — authoritative; "
        "do NOT alter these numbers or invent others):\n" + "\n".join(lines)
    )


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

        db_facts = _entity_db_facts(entity.get("slug") or "", name)
        user_prompt = prompts.build_entity_brief_user(name, all_bullets, list(feed_items), db_facts)
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


def backfill(days: int = 30) -> int:
    """Generate a brief for every entity seen across the last N days of digests.

    For coverage before the next digest cron runs. Each entity is generated EXACTLY ONCE,
    from the MOST RECENT digest it appears in (newest→oldest walk, skip already-seen) —
    `_generate_brief` pulls its own 14-day history, so the latest appearance yields the
    freshest brief without redundant LLM calls.
    """
    from datetime import date as _date

    rows = db.get_digest_contents_for_dedup(days=days)
    rows = sorted(rows, key=lambda r: r[0], reverse=True)  # newest first

    seen: set[str] = set()
    stored = 0
    for d, content in rows:
        if not content:
            continue
        dd = d if isinstance(d, _date) else _date.fromisoformat(str(d)[:10])
        plain = _TAG_RE.sub(" ", content)
        today_bullets = _parse_bullets(content)
        pending = [e for e in _find_entities_in_text(plain) if e["slug"] not in seen]
        if not pending:
            continue
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_generate_brief, e, today_bullets, dd): e for e in pending}
            for fut in as_completed(futures):
                e = futures[fut]
                seen.add(e["slug"])
                try:
                    result = fut.result()
                    if result:
                        db.upsert_entity_intel_brief(
                            result["name"], result["brief_html"], result["model_used"], dd)
                        stored += 1
                except Exception:
                    log.warning("entity brief: backfill upsert failed for %r", e.get("name"), exc_info=True)
    log.info("entity briefs: backfill stored %d briefs (%d unique entities) across %d digests",
             stored, len(seen), len(rows))
    return stored


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Entity intel brief generation")
    ap.add_argument("--backfill", action="store_true",
                    help="regenerate briefs for every entity across recent digests")
    ap.add_argument("--days", type=int, default=30, help="lookback window for --backfill")
    args = ap.parse_args()

    if args.backfill:
        backfill(days=args.days)
    else:
        ap.error("nothing to do: pass --backfill (post-digest generation runs automatically)")
