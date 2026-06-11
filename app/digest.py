"""Daily Digest: read recent feed + cache, summarize, persist.

Mirrors "Crypto Twitter Digest v2": 24h freshness merge, single-row cache, and a
row appended to crypto_digest.

Intelligence layer additions:
  - Entity context: alias-matched entities from today's feed enriched with TVL + analyst state.
  - Digest chain: last DIGEST_CHAIN_DAYS days of digests as reference context.
  - Analyst notes: last ANALYST_NOTES_DAYS days of extracted themes.
  - Post-digest scratchpad: after each successful run, extract themes + entity updates.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from . import analyst, config, db, entities, entity_brief, llm, prompts, scoring
from .telegram_html import sanitize

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")

# ── HTML bullet parser (mirrors web/lib/digest.js parseDigest) ─────────────
_HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'", "&nbsp;": " ",
}


def _decode(s: str) -> str:
    return re.sub(r"&[a-z#0-9]+;", lambda m: _HTML_ENTITIES.get(m.group(), m.group()), s)


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def _parse_digest_bullets(html: str) -> list[dict]:
    """Parse Telegram-HTML digest into [{title, body, link}] — mirrors JS parseDigest."""
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
        title = _decode(_strip_tags(title_m.group(1) if title_m else "")).strip()
        body  = re.sub(r"\s+", " ", _decode(_strip_tags(rest))).strip()
        if title:
            bullets.append({"title": title, "body": body,
                             "link": link_m.group(1) if link_m else None})
    return bullets


def _post_filter_duplicates(html: str, covered_bullets: list[dict]) -> tuple[str, int]:
    """Remove any bullet whose normalized title matches a previously covered story semantically.

    This is the deterministic backstop for cases where the LLM ignores the
    ALREADY COVERED prompt instruction.  Returns (filtered_html, n_removed).
    """
    if not covered_bullets:
        return html, 0

    covered_word_sets = [scoring.get_title_words(b["title"]) for b in covered_bullets if b.get("title")]
    covered_word_sets = [w for w in covered_word_sets if w]

    out, removed = [], 0
    for line in html.split("\n"):
        t = line.strip()
        if t.startswith("•"):
            m = re.search(r"<b>([\s\S]*?)</b>", t, re.I)
            if m:
                title = _decode(_strip_tags(m.group(1))).strip()
                words = scoring.get_title_words(title)
                if words and any(scoring.is_semantic_duplicate(words, cw) for cw in covered_word_sets):
                    log.info("post-filter removed duplicate: %r", title)
                    removed += 1
                    continue
        out.append(line)
    return "\n".join(out), removed


def _build_dedup_context(
    digest_rows: list[tuple],
) -> tuple[set[str], list[dict]]:
    """Parse (date, content) digest rows → (cited_urls, covered_bullets).

    cited_urls:      every <a href="..."> URL cited in those digests (for pre-filtering feed items)
    covered_bullets: [{date, title}] of every bullet title (for prompt injection)
    """
    href_re = re.compile(r'href="([^"]+)"')
    cited_urls: set[str] = set()
    covered: list[dict] = []
    for d, content in digest_rows:
        if not content:
            continue
        cited_urls.update(href_re.findall(content))
        for bullet in _parse_digest_bullets(content):
            if bullet.get("title"):
                covered.append({"date": str(d), "title": bullet["title"]})
    return cited_urls, covered


def _clean_text(content: str) -> str:
    text = _TAG_RE.sub("", content or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace('"', "'")
    return text[:2000]


def _format_items(rows: list[dict]) -> str:
    blocks = []
    for r in rows:
        text = _clean_text(r.get("content", ""))
        if len(text) <= 40:
            continue
        blocks.append(
            f"TYPE: {(r.get('source_type') or '').upper()}\n"
            f"TEXT: {text}\n"
            f"LINK: {r.get('link', '')}\n"
            f"CREATOR: {r.get('creator', '')}"
        )
    return "\n\n---\n\n".join(blocks)


def _is_cache_fresh(last_run, last_analysis: str) -> bool:
    if not last_run or not last_analysis:
        return False
    age = datetime.now(timezone.utc) - last_run
    return age.total_seconds() < config.CACHE_TTL_HOURS * 3600


_BULLET_LINE_RE = re.compile(r"^\s*•")


def _keep_bullets_only(body: str) -> str:
    """Keep only the • bullet lines — drops any preamble/recap/reasoning a weaker
    fallback model may emit before the bullets (defensive against prompt echo)."""
    return "\n".join(ln for ln in (body or "").split("\n") if _BULLET_LINE_RE.match(ln))


def _count_bullets(body: str) -> int:
    return sum(1 for ln in (body or "").split("\n") if _BULLET_LINE_RE.match(ln))


def build_digest() -> tuple[str, str, str]:
    """Run the model. Returns (telegram_html, raw_analysis, model_used)."""
    last_run, last_analysis = db.get_cache()
    rows = db.get_recent_feed_items(config.DIGEST_WINDOW_HOURS, config.DIGEST_LIMIT)
    previous = last_analysis if _is_cache_fresh(last_run, last_analysis) else ""

    # Dedup: parse last 7 days of digests → cited URLs + covered bullet titles
    try:
        dedup_rows = db.get_digest_contents_for_dedup(days=7)
        covered_urls, covered_bullets = _build_dedup_context(dedup_rows)
    except Exception:
        log.debug("could not build dedup context", exc_info=True)
        covered_urls, covered_bullets = set(), []

    # Pre-filter: drop feed items whose exact URL was already cited in a recent digest
    before = len(rows)
    rows = [r for r in rows if not r.get("link") or r["link"] not in covered_urls]
    if before != len(rows):
        log.info("dedup pre-filter: %d → %d feed items (%d removed)", before, len(rows), before - len(rows))

    tweets = _format_items(rows)

    # TVL context (live chain snapshot)
    try:
        tvl_rows = db.get_latest_tvl()
    except Exception:
        log.debug("could not fetch TVL for digest context", exc_info=True)
        tvl_rows = []
    tvl_ctx = prompts.format_tvl_context(tvl_rows)

    # Entity context: alias-match today's feed items → enrich with TVL + analyst state
    try:
        entity_ctx = entities.build_entity_context(rows)
    except Exception:
        log.debug("could not build entity context", exc_info=True)
        entity_ctx = ""

    # Analyst notes: last N days of extracted ongoing themes
    try:
        notes_ctx = analyst.format_analyst_notes()
    except Exception:
        log.debug("could not load analyst notes", exc_info=True)
        notes_ctx = ""

    # Digest chain: last N days of past digests as reference
    try:
        chain_ctx = analyst.format_digest_chain()
    except Exception:
        log.debug("could not load digest chain", exc_info=True)
        chain_ctx = ""

    # Podcast intelligence: notable claims/predictions from recent episode analyses
    try:
        podcast_rows = db.get_recent_podcast_summaries(config.PODCAST_DIGEST_WINDOW_HOURS)
        podcast_ctx = prompts.format_podcast_context(podcast_rows)
    except Exception:
        log.debug("could not load podcast context", exc_info=True)
        podcast_ctx = ""

    user = prompts.build_digest_user(
        tweets,
        previous,
        tvl_context=tvl_ctx,
        entity_context=entity_ctx,
        digest_chain=chain_ctx,
        analyst_notes=notes_ctx,
        podcast_context=podcast_ctx,
        covered_bullets=covered_bullets,
    )
    # Generate; if the model returns 0 parseable bullets (a weak/garbled response),
    # retry once before giving up so a transient bad generation never persists an
    # empty digest. ``body`` is reduced to bullet lines only (anti prompt-echo).
    model = ""
    body = ""
    for attempt in range(2):
        raw, model = llm.complete(prompts.DIGEST_SYSTEM, user, temperature=0.5)
        body = _keep_bullets_only(sanitize(raw))
        body, n_removed = _post_filter_duplicates(body, covered_bullets)
        if n_removed:
            log.info("post-filter: removed %d duplicate bullet(s)", n_removed)
        if _count_bullets(body) >= 1:
            break
        log.warning("digest: 0 parseable bullets (attempt %d/2) — retrying", attempt + 1)
    if _count_bullets(body) < 1:
        raise ValueError("digest produced no parseable bullets after retry")
    html = f"🧠 <b>Crypto Twitter Digest</b>\n\n{body}"
    # Persist the cleaned bullets (not the raw model text) so the cache + analyst
    # extraction never carry preamble/leak forward.
    return html, body, model


def persist_digest(raw_analysis: str, model_used: str = "", trigger: str = "manual",
                   duration_ms: int | None = None, error: str | None = None) -> None:
    today = datetime.now(timezone.utc).date()
    md = f"# Crypto Digest {today.isoformat()}\n\n{raw_analysis}\n"
    if not error:
        db.set_cache(raw_analysis)
    db.insert_digest(today, md, model_used=model_used, trigger=trigger,
                     duration_ms=duration_ms, error=error)


def orchestrate_post_digest(html: str, raw_digest: str, trigger: str) -> None:
    """Run all post-digest side effects sequentially with retries and tracking.
    
    1. Analyst Notes & Entity summary updates
    2. Bullet analysis generation (pre-computes web UI analysis & importance scoring)
    3. Entity intel briefs update
    4. Entity memory decay (inactive entities/pruning)
    5. Narrative layer rebuild (clusters + momentum)
    """
    today = datetime.now(timezone.utc).date()
    log.info("Starting post-digest orchestration for %s (trigger=%s)...", today.isoformat(), trigger)

    def run_step(name: str, func, max_retries: int = 2):
        for attempt in range(1, max_retries + 1):
            try:
                log.info("Running post-digest step: %s (attempt %d/%d)", name, attempt, max_retries)
                func()
                log.info("Completed post-digest step: %s", name)
                return True
            except Exception as exc:
                log.warning("Post-digest step %s failed (attempt %d/%d): %s", name, attempt, max_retries, exc, exc_info=True)
                if attempt < max_retries:
                    time.sleep(2)
        log.error("Post-digest step %s completely failed after %d attempts", name, max_retries)
        return False

    # Step 1: Analyst extraction
    run_step("analyst notes extraction", lambda: analyst.extract_and_persist(raw_digest, source=trigger))

    # Step 2: Bullet analysis & Scoring
    run_step("bullet analysis and scoring", lambda: generate_and_store_bullet_analyses(today, html))

    # Step 3: Entity briefs
    run_step("entity brief updates", lambda: entity_brief.update_entity_briefs_from_digest(today, html))

    # Step 4: Decay stale entities
    run_step("decay stale entities", lambda: db.decay_stale_entities())

    # Step 5: Narratives rebuild
    def rebuild_narratives():
        from . import narratives
        narratives.build_and_store()
    run_step("narrative rebuild", rebuild_narratives)

    log.info("Post-digest orchestration finished for %s.", today.isoformat())


def run_digest(trigger: str = "manual") -> str:
    """Build, persist, and return the Telegram-HTML digest.

    After a successful run, kicks off the consolidated post-processing orchestrator.
    """
    t0 = time.monotonic()
    try:
        html, raw, model = build_digest()
        duration_ms = int((time.monotonic() - t0) * 1000)
        persist_digest(raw, model_used=model, trigger=trigger, duration_ms=duration_ms)

        # Consolidate and orchestrate post-digest processes
        try:
            orchestrate_post_digest(html, raw, trigger)
        except Exception:
            log.warning("post-digest orchestration encountered unexpected error (non-fatal)", exc_info=True)

        return html
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("digest failed (trigger=%s)", trigger)
        try:
            persist_digest("", model_used="", trigger=trigger,
                           duration_ms=duration_ms, error=str(exc))
        except Exception:
            log.exception("failed to persist error digest record")
        raise


def _generate_one_analysis(bullet: dict) -> dict:
    """Call LLM for a single bullet. Returns the bullet dict enriched with analysis + model_used."""
    title = bullet["title"]
    body = bullet.get("body", "")
    
    # Retrieve ground truth context for matched entities to prevent hallucinations.
    # Two tiers, kept SEPARATE on purpose: live DeFiLlama TVL + Snapshot governance are
    # real DB rows (trustworthy), whereas entity_memory.summary is itself an earlier LLM
    # guess. Presenting the latter as "ground truth" would launder a past hallucination
    # into this prompt, so it is labeled as an unverified hint the model must not quote.
    from . import entities
    slugs = entities.detect_entities_in_text(f"{title} {body}")

    verified_lines: list[str] = []
    note_lines: list[str] = []
    if slugs:
        entity_rows = db.get_entities_by_slugs(slugs)
        try:
            tvl_map = {r["slug"]: r for r in db.get_protocols_by_slugs(slugs)}
        except Exception:
            tvl_map = {}
            
        def fmt_usd(v: float) -> str:
            if v >= 1e12:
                return f"${v / 1e12:.2f}T"
            if v >= 1e9:
                return f"${v / 1e9:.1f}B"
            if v >= 1e6:
                return f"${v / 1e6:.0f}M"
            return f"${v:,.0f}"

        for ent in entity_rows:
            slug = ent["slug"]
            name = ent.get("name") or slug
            summary = (ent.get("summary") or "").strip()
            tvl_info = tvl_map.get(slug)
            
            # ── Verified facts: live DeFiLlama TVL + Snapshot governance ──
            fact_parts = [name]
            if tvl_info:
                tvl = tvl_info.get("tvl_usd") or 0
                chg = tvl_info.get("tvl_change_7d")
                cat = tvl_info.get("category") or ""
                chg_str = f" ({chg:+.1f}% 7d)" if chg is not None else ""
                fact_parts.append(f"TVL {fmt_usd(float(tvl))}{chg_str}")
                if cat:
                    fact_parts.append(f"Category: {cat}")
            try:
                props = db.get_governance_for_entity(slug, name)
                if props:
                    prop_titles = [f"'{p['title']}' ({p['state']})" for p in props]
                    fact_parts.append(f"Recent governance: {', '.join(prop_titles)}")
            except Exception:
                pass
            if len(fact_parts) > 1:  # at least one real fact beyond the name
                verified_lines.append(" - " + " | ".join(fact_parts))

            # ── Unverified: prior analyst summary (earlier model output) ──
            if summary:
                note_lines.append(f" - {name}: {summary}")

    ctx_blocks: list[str] = []
    if verified_lines:
        ctx_blocks.append(
            "VERIFIED DATABASE FACTS (live DeFiLlama TVL + Snapshot governance — use as "
            "background; do NOT alter these numbers or invent others):\n"
            + "\n".join(verified_lines)
        )
    if note_lines:
        ctx_blocks.append(
            "PRIOR ANALYST NOTES (earlier model-generated context — may be stale or "
            "imprecise; use only as a hint, NEVER quote its numbers/dates as fact):\n"
            + "\n".join(note_lines)
        )
    entity_context = "\n\n".join(ctx_blocks)

    user = prompts.build_bullet_analyst_user(title, body)
    if entity_context:
        user = f"{entity_context}\n\n{user}"
        
    content, model = llm.complete(prompts.BULLET_ANALYST_SYSTEM, user, max_tokens=350, temperature=0.3)
    return {**bullet, "analysis": content.strip(), "model_used": model}


def generate_and_store_bullet_analyses(digest_date, html: str) -> int:
    """Parse bullets from digest HTML, run LLM for each in parallel, store results.

    Runs up to 5 concurrent LLM calls. Failures per-bullet are logged and skipped.
    Returns count of successfully stored analyses.
    """
    bullets = _parse_digest_bullets(html)
    if not bullets:
        log.warning("bullet analysis: no bullets parsed from digest")
        return 0

    # max_workers=3 (was 5): the per-bullet analyst views stay individual (full depth)
    # but a gentler burst avoids free-tier 429s, esp. on the slower NIM primary.
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_generate_one_analysis, b): b for b in bullets}
        for fut in as_completed(futures):
            b = futures[fut]
            try:
                results.append(fut.result())
            except Exception:
                log.warning("bullet analysis: LLM failed for %r", b["title"], exc_info=True)

    if not results:
        return 0

    # Importance scoring (best-effort — must never break the digest). Merges
    # importance_score / source_count / score_breakdown into each result by title.
    try:
        scored = scoring.compute_importance_scores(
            [{"title": b["title"], "body": b.get("body", "")} for b in bullets],
            str(digest_date),
        )
        by_title = {s["title"]: s for s in scored}
        for r in results:
            s = by_title.get(r["title"])
            if s:
                r["importance_score"] = s.get("importance_score")
                r["source_count"] = s.get("source_count")
                r["score_breakdown"] = s.get("score_breakdown")
    except Exception:
        log.warning("bullet analysis: importance scoring failed (non-fatal)", exc_info=True)

    stored = db.upsert_bullet_analyses(digest_date, results)
    # Drop rows left by superseded same-day runs (titles no longer in this digest).
    # Keyed on ALL current bullet titles (not just `results`) so a bullet whose analysis
    # failed this run keeps any prior row. Only runs after a successful generation.
    try:
        pruned = db.prune_bullet_analyses(digest_date, [b["title"] for b in bullets])
        if pruned:
            log.info("bullet analysis: pruned %d stale row(s) for %s", pruned, digest_date)
    except Exception:
        log.debug("bullet analysis: prune failed", exc_info=True)
    log.info("bullet analysis: %d/%d stored for %s", stored, len(bullets), digest_date)
    return stored


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-persist", action="store_true", help="build only, don't write cache/digest")
    ap.add_argument("--regen-analyses", action="store_true",
                    help="Regenerate pre-computed bullet analyses for all recent digests")
    ap.add_argument("--from-date", metavar="YYYY-MM-DD",
                    help="Limit --regen-analyses to dates on or after this date")
    args = ap.parse_args()

    if args.regen_analyses:
        from datetime import date as _date
        with db._conn() as _conn:
            with _conn.cursor() as _cur:
                _cur.execute(
                    """SELECT DISTINCT ON (date) date, content
                       FROM crypto_digest WHERE error IS NULL
                       ORDER BY date DESC, created_at DESC"""
                )
                rows = _cur.fetchall()
        if args.from_date:
            cutoff = _date.fromisoformat(args.from_date)
            rows = [(d, c) for d, c in rows if d >= cutoff]
        rows.sort(key=lambda r: r[0])  # oldest first
        print(f"Regenerating bullet analyses for {len(rows)} digest(s)…")
        for d, content in rows:
            deleted = db.delete_bullet_analyses(d)
            n = generate_and_store_bullet_analyses(d, content)
            print(f"  {d}: {deleted} deleted → {n} generated")
        print("Done.")

    elif args.no_persist:
        html, _, _ = build_digest()
        print(html)
    else:
        print(run_digest())
