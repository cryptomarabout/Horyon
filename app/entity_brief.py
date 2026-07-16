"""Entity Intel Briefs: pre-compute per-entity updates post-digest.

Triggered once per day after the daily digest runs. For every entity the SHARED
runtime matcher (entities.detect_entities_in_text — word boundaries, generic-term
rejection, case-sensitive ambiguous brands) finds in that day's bullets, generates
a brief using:
  - Today's digest bullets mentioning the entity
  - Historical digest bullets (last 14 days)
  - Recent feed items (semantic search, last 14 days — anchor-gated to items that
    actually NAME the entity, since ANN search always returns topk)

Stored in entity_intel_brief; served by specialized.py and /api/search as an
instant cache hit that bypasses the full ReAct loop.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_t, datetime, timedelta, timezone

from . import db, entities, llm, prompts, util

log = logging.getLogger(__name__)

# A reasoning fallback model (e.g. nemotron) emits its chain-of-thought ("We need to
# produce an intel brief…") before — or instead of — the brief. Strip <think> blocks
# (llm.strip_think) and any preamble before the first 🔎 header / • bullet, then require
# ≥1 bullet, else reject (a cached reasoning-leak is worse than a cache miss → falls
# through to the live agent).

# Template-echo / planning-leak rejection (2026-07-07 incident): nemotron echoed the
# ENTITY_BRIEF_SYSTEM output template ITSELF — which contains a literal bullet line
# ("• <b>Short title</b> — What happened. Why it matters.") — followed by its planning prose
# ("We need to prioritize hacks/exploits…"). The header+bullet shape satisfied the structural
# "has a •" check below, so 6 garbage briefs (Morpho, Jito, Securitize…) were persisted and
# served on /api/search. Phrases are anchored to THIS prompt's template text plus the planning
# vocabulary proven in digest._ANALYSIS_LEAK_RE — validated against all 540 stored briefs:
# exactly the 6 contaminated rows match, 0 false positives.
_BRIEF_LEAK_RE = re.compile(
    r"Short title|What happened\. Why it matters|3[–-]5 bullets|"
    r"\bwe need to (?:write|produce|generate|create|extract|summarize|prioriti[sz]e|order|pick|include|list)\b|"
    r"\bwe should (?:prioriti[sz]e|order|pick|include)\b|"
    r"\bi need to (?:write|produce|generate|list|order)\b|"
    r"\blet me (?:think|write|draft|check|parse|list)\b|"
    r"\bthe user wants\b|\bprioriti[sz]e hacks\b",
    re.IGNORECASE,
)


_decode = util.decode_entities


_DASH_BULLET_RE = re.compile(r"^-\s+")


def _normalize_brief_format(text: str) -> str:
    """Canonicalize formatting drift the model sometimes emits despite the prompt's
    explicit template. Observed drift (measured on 2026-07-05: 0 of 20 sampled briefs
    had a line starting with '•', so this isn't a rare edge case — it's the norm):
      1. Header + first bullet merged on one line via an inline ' • ' separator
         ("🔎 <b>Aave</b> • <b>Title</b> — body...") instead of the header standing
         alone with the bullet starting its own line.
      2. Continuation bullets marked with a leading '-' instead of '•'.
    Both are invisible to a reader but break every consumer that keys off a literal
    line-leading '•' (web SearchPanel, Telegram render) — those consumers render
    NOTHING for a line they don't recognize as a bullet, rather than erroring, so a
    drifted brief silently looked like an empty result. Fix at the source so every
    consumer can keep the simple "line starts with •" contract."""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if s.startswith("🔎") and " • " in s:
            head, _, rest = s.partition(" • ")
            out.append(head)
            out.append("")
            out.append("• " + rest)
        elif _DASH_BULLET_RE.match(s):
            out.append("• " + _DASH_BULLET_RE.sub("", s))
        else:
            out.append(ln)
    return "\n".join(out)


def _clean_brief(content: str) -> str:
    """Keep only the brief itself: the 🔎 header + • bullet lines, dropping leaked reasoning."""
    if not content:
        return ""
    text = llm.strip_think(content)
    kept, started = [], False
    for ln in text.splitlines():
        s = ln.strip()
        if not started:
            if s.startswith("🔎") or s.startswith("•"):
                started = True
            else:
                continue  # skip preamble / planning lines
        kept.append(ln)
    text = _normalize_brief_format("\n".join(kept).strip())
    # Reject the whole completion when what survived still reads as a template echo or
    # planning prose (see _BRIEF_LEAK_RE) — a dropped brief beats a served one, and the
    # /api/search route falls back to a deterministic feed render on a cache miss.
    if _BRIEF_LEAK_RE.search(text):
        return ""
    return text


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


def _chunks_for_matcher(text: str, size: int = 280) -> list[str]:
    """Word-aligned windows ≤ ``size`` chars, with a small tail overlap.

    entities.detect_entities_in_items scans only the first ~300 chars of each item
    (its plain_text limit), so a whole digest passed as one string would silently lose
    every entity after the first bullets. The overlap keeps a name that straddles a
    window boundary matchable."""
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    out: list[str] = []
    cur = ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > size:
            out.append(cur)
            cur = " ".join(cur.split()[-5:] + [w])
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        out.append(cur)
    return out


def _find_entities_in_text(plain_text: str) -> list[dict]:
    """Return every entity_memory row that appears in text (full rows: slug, name,
    type, aliases — the aliases feed the per-entity anchor pattern below).

    Uses the SHARED runtime matcher (entities.detect_entities_in_items over
    word-aligned chunks) so brief coverage obeys the same false-positive gates as
    every other surface — word boundaries, generic-vocabulary rejection, and
    CASE-SENSITIVE matching for ambiguous single-word brands (Flow/Exodus). The
    previous hand-rolled IGNORECASE loop matched 'Flow' in "cash flow" and generated
    a persisted brief about the wrong entity from unrelated text.
    """
    try:
        items = [{"content": c} for c in _chunks_for_matcher(plain_text)]
        if not items:
            return []
        slugs = entities.detect_entities_in_items(items)
        if not slugs:
            return []
        return [dict(r) for r in db.get_entities_by_slugs(slugs)]
    except Exception:
        log.warning("entity brief: shared entity detection failed", exc_info=True)
        return []


def _entity_anchor(entity: dict) -> "re.Pattern | None":
    """Word-boundary pattern over the entity's name + distinctive aliases — the gate
    that keeps semantic-search results honest (ANN always returns topk, so without it
    topically-adjacent items that never mention the entity enter the prompt and get
    ATTRIBUTED to it). Ambiguous single-word brand names (Flow, Exodus, AERO) match
    case-sensitively, mirroring the shared runtime matcher. None when no usable term."""
    ci_terms: set[str] = set()
    cs_terms: set[str] = set()
    for term in [entity.get("name") or "", *(entity.get("aliases") or [])]:
        t = (term or "").strip()
        if not t or t.startswith("@") or len(t) < 3 or t.lower() in entities.GENERIC_TERMS:
            continue
        if entities._ambiguous_single_word(t):
            cs_terms.add(t)
        else:
            ci_terms.add(t)
    parts: list[str] = []
    if ci_terms:
        parts.append("(?i:" + "|".join(re.escape(t) for t in sorted(ci_terms)) + ")")
    for t in sorted(cs_terms):
        parts.append(re.escape(t) + "|" + re.escape(t.upper()))
    if not parts:
        return None
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


def _get_historical_bullets_for_entity(anchor: "re.Pattern", days: int = 14) -> list[dict]:
    """Return digest bullets from the last N days (excluding today) matching the anchor."""
    rows = db.get_digest_contents_for_dedup(days=days)
    matching: list[dict] = []
    for d, content in rows:
        if not content:
            continue
        for bullet in _parse_bullets(content):
            text = bullet.get("title", "") + " " + bullet.get("body", "")
            if anchor.search(text):
                matching.append({"date": str(d), **bullet})
    return matching


_fmt_usd = util.fmt_usd


def _fmt_price(v: float) -> str:
    return f"${v:,.2f}" if v >= 1 else f"${v:,.4g}"


def _fmt_num(v: float) -> str:
    if v >= 1e12:
        return f"{v / 1e12:.2f}T"
    if v >= 1e9:
        return f"{v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    return f"{v:,.0f}"


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
    try:
        # Joined by gecko_id == slug — only ever populated when our slug happens to
        # equal CoinGecko's own coin id (see coingecko_market's schema comment). An
        # entity whose token isn't top-500 by market cap, or whose slug diverges,
        # simply gets no market line here — never a fabricated one.
        mkt = {m["gecko_id"]: m for m in db.get_market_data_by_slugs([slug])}.get(slug)
        if mkt and mkt.get("market_cap_usd") is not None:
            bits = []
            if mkt.get("price_usd") is not None:
                bits.append(f"price {_fmt_price(mkt['price_usd'])}")
            bits.append(f"mcap {_fmt_usd(mkt['market_cap_usd'])}")
            if mkt.get("fdv_usd"):
                bits.append(f"FDV {_fmt_usd(mkt['fdv_usd'])}")
            chg = mkt.get("price_change_7d_pct")
            if chg is not None:
                bits.append(f"{chg:+.1f}% 7d")
            if mkt.get("circulating_supply") and mkt.get("total_supply"):
                pct_circ = mkt["circulating_supply"] / mkt["total_supply"] * 100
                sym = mkt.get("symbol") or ""
                bits.append(
                    f"{_fmt_num(mkt['circulating_supply'])}/{_fmt_num(mkt['total_supply'])} "
                    f"{sym} circulating ({pct_circ:.0f}%)"
                )
            lines.append(f" - {name} token: " + ", ".join(bits))
    except Exception:
        log.debug("entity brief: market data fetch failed for %r", name, exc_info=True)
    if not lines:
        return ""
    return (
        "VERIFIED DATABASE FACTS (live DeFiLlama TVL + CoinGecko market data + Snapshot "
        "governance — authoritative; do NOT alter these numbers or invent others):\n"
        + "\n".join(lines)
    )


def _generate_brief(entity: dict, today_bullets: list[dict], digest_date: date_t) -> dict | None:
    """Generate brief HTML for one entity. Returns enriched entity dict or None on failure."""
    name = entity["name"]
    try:
        # One anchor pattern gates ALL text matched to this entity (today's bullets,
        # history, semantic-search results) — name + distinctive aliases, with the
        # ambiguous-brand case rule. Fallback to the plain name pattern keeps the
        # brief building if the entity row carries no usable terms.
        anchor = _entity_anchor(entity) or re.compile(
            r"\b" + re.escape(name) + r"\b", re.IGNORECASE)

        # Filter today's bullets for this entity
        entity_today = [
            {"date": str(digest_date), **b}
            for b in today_bullets
            if anchor.search(b.get("title", "") + " " + b.get("body", ""))
        ]

        # Historical bullets (last 14 days, excluding today)
        hist_bullets = _get_historical_bullets_for_entity(anchor, days=14)

        all_bullets = entity_today + hist_bullets

        # Semantic search is ANN — it ALWAYS returns topk, so without the anchor gate
        # topically-adjacent items that never mention the entity would enter the prompt
        # as "RECENT FEED ITEMS" and their events get attributed to this entity
        # (the same lesson as digest._related_coverage). Fail-open to the unfiltered
        # list: a degraded gate must not kill the brief.
        feed_items = list(db.search_feed(name, topk=10, days=14))
        try:
            feed_items = [r for r in feed_items
                          if anchor.search(util.TAG_RE.sub(" ", str(r.get("content") or "")))]
        except Exception:
            log.warning("entity brief: feed-item anchor gate failed for %r — using "
                        "unfiltered search results", name, exc_info=True)

        if not all_bullets and not feed_items:
            log.debug("entity brief: no data for %r, skipping", name)
            return None

        db_facts = _entity_db_facts(entity.get("slug") or "", name)
        user_prompt = prompts.build_entity_brief_user(name, all_bullets, list(feed_items), db_facts)
        # 900 tokens (was 600): a reasoning fallback model needs room to think AND still
        # emit the brief; with 600 it burned the budget reasoning and produced no bullets.
        # skip_reasoning: reasoning models are excluded from this path outright since the
        # 2026-07-07 template-echo incident — _BRIEF_LEAK_RE is the backstop, not the plan.
        content, model = llm.complete(prompts.ENTITY_BRIEF_SYSTEM, user_prompt, max_tokens=900,
                                      temperature=0.4, skip_reasoning=True)
        brief = _clean_brief(content)
        # Deterministic backstop: unwrap any link that isn't a real source URL from the
        # context (planted link-swap injection or invented canonical URL) — same guard the
        # digest applies. Allowed = the SOURCE links actually fed into the prompt.
        allowed_links = ({b.get("link") for b in all_bullets if b.get("link")}
                         | {r.get("link") for r in feed_items if r.get("link")})
        brief = util.strip_foreign_hrefs(brief, allowed_links)
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
    plain_text   = util.TAG_RE.sub(" ", bullets_html)
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


def refresh_stale_briefs(hours: int = 3, min_new_mentions: int = 2, max_refresh: int = 15) -> int:
    """Incremental intra-day top-up: refresh briefs for entities with MEANINGFUL new
    coverage since their last brief, without waiting for the next daily digest.

    Self-limiting by design (a quiet news cycle costs nothing):
      - Only entities that ALREADY have a brief are refreshed — discovering brand-new
        entities stays the daily digest's job (`update_entity_briefs_from_digest`) /
        `backfill`, so this never grows the entity roster or re-runs full extraction.
      - Only entities whose brief predates this window (`updated_at` older than
        `hours` ago) are considered — an entity refreshed earlier this cycle is skipped.
      - Requires >= `min_new_mentions` distinct new feed items anchored to the entity
        in the window — a single passing mention isn't "meaningful" new coverage.
      - Capped at `max_refresh` LLM calls per run regardless of how many qualify, so a
        busy news cycle has a predictable cost ceiling.
    """
    recent = db.get_recent_feed_items(hours=hours, limit=800)
    if not recent:
        return 0

    slugs = entities.detect_entities_in_items([{"content": r.get("content", "")} for r in recent])
    if not slugs:
        return 0

    briefed = {r["entity_name"].lower(): r["updated_at"]
               for r in db._fetchall(
                   "SELECT entity_name, updated_at FROM entity_intel_brief", dict_rows=True)}
    if not briefed:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    plain_recent = [(util.TAG_RE.sub(" ", r.get("content") or "")) for r in recent]

    candidates = []
    for ent in db.get_entities_by_slugs(slugs):
        last_update = briefed.get(ent["name"].lower())
        if last_update is None or last_update > cutoff:
            continue  # no existing brief (not this job's job) or already fresh
        anchor = _entity_anchor(ent)
        if not anchor:
            continue
        new_count = sum(1 for t in plain_recent if anchor.search(t))
        if new_count >= min_new_mentions:
            candidates.append(ent)

    if not candidates:
        return 0
    candidates = candidates[:max_refresh]

    log.info("entity briefs: %d entities qualify for incremental refresh (of %d mentioned)",
              len(candidates), len(slugs))

    today = datetime.now(timezone.utc).date()
    stored = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_generate_brief, e, [], today): e for e in candidates}
        for fut in as_completed(futures):
            e = futures[fut]
            try:
                result = fut.result()
                if result:
                    db.upsert_entity_intel_brief(
                        result["name"], result["brief_html"], result["model_used"], today)
                    stored += 1
            except Exception:
                log.warning("entity brief: incremental refresh failed for %r", e.get("name"), exc_info=True)

    log.info("entity briefs: incremental refresh stored %d/%d briefs", stored, len(candidates))
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
        plain = util.TAG_RE.sub(" ", content)
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


def renormalize_stored_briefs() -> int:
    """Re-apply `_normalize_brief_format` to every stored brief in place — a pure string
    transform, no LLM calls. Run once after deploying the normalizer to fix the existing
    backlog (measured 2026-07-05: effectively 100% of stored briefs pre-date the fix and
    render as empty in the web SearchPanel). Only writes rows that actually change."""
    rows = db._fetchall(
        "SELECT entity_name, brief_html, model_used, digest_date FROM entity_intel_brief",
        dict_rows=True,
    )
    updated = 0
    for r in rows:
        fixed = _normalize_brief_format(r["brief_html"])
        if fixed != r["brief_html"]:
            db.upsert_entity_intel_brief(r["entity_name"], fixed, r["model_used"], r["digest_date"])
            updated += 1
    log.info("entity briefs: renormalized %d / %d stored briefs", updated, len(rows))
    return updated


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Entity intel brief generation")
    ap.add_argument("--backfill", action="store_true",
                    help="regenerate briefs for every entity across recent digests")
    ap.add_argument("--days", type=int, default=30, help="lookback window for --backfill")
    ap.add_argument("--renormalize", action="store_true",
                    help="re-apply format normalization to all stored briefs in place (no LLM calls)")
    args = ap.parse_args()

    if args.renormalize:
        renormalize_stored_briefs()
    elif args.backfill:
        backfill(days=args.days)
    else:
        ap.error("nothing to do: pass --backfill or --renormalize")
