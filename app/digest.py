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
from datetime import datetime, timedelta, timezone

from . import analyst, config, db, entities, entity_brief, llm, prompts, scoring, util
from .telegram_html import sanitize

log = logging.getLogger(__name__)

# ── HTML bullet parser (mirrors web/lib/digest.js parseDigest) ─────────────
_decode = util.decode_entities


def _strip_tags(s: str) -> str:
    return util.TAG_RE.sub("", s)


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


def _saturated_entities(covered_bullets: list[dict], min_days: int = 3) -> list[str]:
    """Display names of protocols covered on ≥ ``min_days`` distinct recent digest days.

    Drives the prompt's OVER-COVERED PROTOCOLS block: the LLM applies a higher inclusion
    bar to these so the daily feed stops stacking yet-another routine Aave/Morpho/Pendle
    update. Best-effort: any failure returns [] (the digest still builds).
    """
    if not covered_bullets:
        return []
    try:
        days_by_slug: dict[str, set] = {}
        for b in covered_bullets:
            title = b.get("title") or ""
            for slug in entities.detect_entities_in_text(title):
                days_by_slug.setdefault(slug, set()).add(b.get("date"))
        hot = [slug for slug, days in days_by_slug.items() if len(days) >= min_days]
        if not hot:
            return []
        names = {r["slug"]: r.get("name") or r["slug"] for r in db.get_entities_by_slugs(hot)}
        # order by saturation (most-covered first)
        hot.sort(key=lambda s: len(days_by_slug[s]), reverse=True)
        return [names.get(s, s) for s in hot]
    except Exception:
        log.debug("could not compute saturated entities", exc_info=True)
        return []


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
    text = util.TAG_RE.sub("", content or "")
    text = util.WS_RE.sub(" ", text).strip()
    text = text.replace('"', "'")
    return text[:2000]


# Per-item quality annotations for the digest prompt, keyed on the ingest-time
# quality_flag (title_content_coherence_check). thin_content is a MIX of genuine
# short signals ("Base just faced a consensus issue…") and pure teaser links
# ("Learn more: gauntlet.xyz/…"), so those items are kept but the model is told
# not to pad them — padding a 2-sentence bullet out of a 60-char teaser is the
# main confabulation vector. boilerplate_title marks multi-story newsletter
# recaps that must not be re-reported as a fresh single event. Self-contained
# NOTE text (no matching rule needed in _DIGEST_RULES); unknown/ok flags add nothing.
_QUALITY_NOTES = {
    "thin_content": (
        "NOTE: LOW-DETAIL SOURCE — a very short post or teaser link. Report ONLY what "
        "TEXT literally states; if it names no concrete event, discard it. Never pad, "
        "infer, or guess what is behind its link."
    ),
    "boilerplate_title": (
        "NOTE: NEWSLETTER/ROUNDUP — a multi-story recap that may restate old news. Do "
        "not report it as a fresh single event; use it only for a story TEXT itself "
        "states, and prefer a dedicated source covering the same story."
    ),
}


def _format_items(rows: list[dict]) -> str:
    blocks = []
    for r in rows:
        text = _clean_text(r.get("content", ""))
        if len(text) <= 40:
            continue
        block = (
            f"TYPE: {(r.get('source_type') or '').upper()}\n"
            f"TEXT: {text}\n"
            f"LINK: {r.get('link', '')}\n"
            f"CREATOR: {r.get('creator', '')}"
        )
        note = _QUALITY_NOTES.get(r.get("quality_flag") or "ok")
        if note:
            block += f"\n{note}"
        blocks.append(block)
    return "\n\n---\n\n".join(blocks)


def _is_cache_fresh(last_run, last_analysis: str) -> bool:
    if not last_run or not last_analysis:
        return False
    age = datetime.now(timezone.utc) - last_run
    return age.total_seconds() < config.CACHE_TTL_HOURS * 3600


_BULLET_LINE_RE = re.compile(r"^\s*•")

# Within a bullet the ONLY legitimate em dash is the title/body separator
# (`• <b>Title</b> — body`). Em/en dashes INSIDE the body are an AI "slop" tell
# (e.g. "No VC discount—bought at market") that reads as garbled on the social
# card + thread, so normalize them to commas — keeping a numeric range (3—5) as a
# plain hyphen. Regular hyphens (on-chain, 4-year) and URL hyphens are untouched
# (the classes below match only em/en dashes, never ASCII "-").
_BODY_NUM_DASH = re.compile(r"(?<=\d)\s*[—–]\s*(?=\d)")
_BODY_DASH = re.compile(r"\s*[—–]+\s*")
_TITLE_END_RE = re.compile(r"</b>", re.I)
_LEAD_SEP_RE = re.compile(r"\s*[—–-]+\s*")


def _deslop_bullet_body(line: str) -> str:
    """De-slop one bullet line: preserve `<b>Title</b> — ` then strip em/en dashes
    out of the body prose. No-op on a line without a title (leaves it untouched)."""
    m = _TITLE_END_RE.search(line)
    if not m:
        return line
    head, tail = line[: m.end()], line[m.end():]
    sep_m = _LEAD_SEP_RE.match(tail)          # the intentional title/body separator
    sep = " — " if sep_m else ""
    rest = tail[sep_m.end():] if sep_m else tail
    rest = _BODY_NUM_DASH.sub("-", rest)
    rest = _BODY_DASH.sub(", ", rest)
    return f"{head}{sep}{rest}"


def _keep_bullets_only(body: str) -> str:
    """Keep only the • bullet lines (drops any preamble/recap/reasoning a weaker
    fallback model may emit before the bullets — defensive against prompt echo) AND
    de-slop em dashes inside each bullet body, so NO digest-persist path ever stores
    an AI-tell em dash that then leaks into the web/OG card/thread."""
    return "\n".join(
        _deslop_bullet_body(ln)
        for ln in (body or "").split("\n")
        if _BULLET_LINE_RE.match(ln)
    )


def _count_bullets(body: str) -> int:
    return sum(1 for ln in (body or "").split("\n") if _BULLET_LINE_RE.match(ln))


# Anti-injection/hallucination href allowlist lives in util (shared with the entity brief).
_strip_foreign_hrefs = util.strip_foreign_hrefs
_norm_link = util.norm_link


# Patterns that indicate the LLM emitted placeholder or garbled output.
_PLACEHOLDER_RE = re.compile(r"\bN/A\b|\[INSERT\]|\[YOUR\b|as of writing|I don't know", re.IGNORECASE)
_RAW_MARKDOWN_RE = re.compile(r"\*\*")
_BOILERPLATE_TITLE_RE = re.compile(
    r"<b>[\w\s]+(newsletter|briefing|digest|update|roundup)\s*#?\d*</b>", re.IGNORECASE
)


def validate_digest_output(content: str) -> list[str]:
    """Structural validation of a generated digest.

    Returns a list of error strings.  Empty list = valid output.
    Severity is encoded as the first word: HIGH / MEDIUM / LOW.
    The build_digest() caller retries once on any HIGH error.
    """
    errors: list[str] = []
    if not content or not content.strip():
        errors.append("HIGH: digest is empty")
        return errors

    bullets = _parse_digest_bullets(content)
    if len(bullets) < 5:
        errors.append(f"HIGH: only {len(bullets)} parseable bullet(s) — expected ≥5")

    if _PLACEHOLDER_RE.search(content):
        errors.append("HIGH: placeholder or confession text found in output")

    if _RAW_MARKDOWN_RE.search(content):
        errors.append("MEDIUM: raw markdown '**' found — prompt echo or wrong format")

    # Check for repeated bullet titles (dedup failure)
    titles = [b["title"].lower().strip() for b in bullets if b.get("title")]
    seen: set[str] = set()
    for t in titles:
        if t in seen:
            errors.append(f"MEDIUM: duplicate bullet title: {t!r}")
        seen.add(t)

    # Flag boilerplate site-name titles leaking through
    for b in bullets:
        if b.get("title") and _BOILERPLATE_TITLE_RE.search(f"<b>{b['title']}</b>"):
            errors.append(f"LOW: boilerplate-style bullet title: {b['title']!r}")

    # Empty bullets (title present but no body content at all)
    for b in bullets:
        if b.get("title") and not (b.get("body") or "").strip():
            errors.append(f"LOW: empty bullet body for title {b['title']!r}")

    return errors


def build_digest() -> tuple[str, str, str]:
    """Run the model. Returns (telegram_html, raw_analysis, model_used)."""
    last_run, last_analysis = db.get_cache()
    rows = db.get_recent_feed_items(config.DIGEST_WINDOW_HOURS, config.DIGEST_LIMIT)
    log.info("digest: %d feed item(s) in pub_date window (last %dh)",
             len(rows), config.DIGEST_WINDOW_HOURS)
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
        saturated_entities=_saturated_entities(covered_bullets),
    )
    # Generate; validate output structure; retry once on HIGH-severity failures.
    model = ""
    body = ""
    # Allowlist of legitimate source URLs = the input items' own LINK fields. Any output
    # <a href> outside this set is a planted (link-swap injection) or invented URL and is
    # unwrapped to plain text (deterministic backstop to the prompt's UNTRUSTED INPUT rail).
    allowed_links = {r["link"] for r in rows if r.get("link")}
    for attempt in range(2):
        raw, model = llm.complete(prompts.DIGEST_SYSTEM, user, temperature=0.5)
        body = _keep_bullets_only(sanitize(raw))
        body = _strip_foreign_hrefs(body, allowed_links)
        body, n_removed = _post_filter_duplicates(body, covered_bullets)
        if n_removed:
            log.info("post-filter: removed %d duplicate bullet(s)", n_removed)

        errs = validate_digest_output(body)
        high_errs = [e for e in errs if e.startswith("HIGH")]
        if errs:
            sev = "HIGH" if high_errs else "MEDIUM/LOW"
            log.warning("digest: validation %s errors (attempt %d/2): %s",
                        sev, attempt + 1, "; ".join(errs))
        if not high_errs:
            break
        log.warning("digest: retrying due to HIGH-severity validation failure")

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
    6. Twitter/X thread rendering (one tweet per bullet + OG card) — consumes step 2's output
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

    # Step 6: Twitter/X thread rendering (reads step 2's stored analyses + scores)
    def build_thread():
        from . import threads
        threads.build_thread_for_date(today)
    run_step("twitter thread render", build_thread)

    # Step 6b: Pre-render + cache the /api/og social card (reads step 2's scores for ranking).
    # Renders once here so the PUBLIC og route serves stored bytes instead of rendering per
    # request — removes a compute-DoS vector on the public site (app/og_cache.py).
    def cache_og():
        from . import og_cache
        og_cache.build_og_for_date(today)
    run_step("og card cache", cache_og)

    # Step 7: Audio briefings — all three length variants (short/standard/explainer) from one set
    # of grounded signals; also consumes step 2's analyses. Last because it's the newest/most
    # failure-prone surface and depends on nothing downstream; its own internal timeout + the
    # run_step isolation keep it from delaying/breaking the digest. (Each variant fails closed on
    # its own modality gate, so one blocked render never affects the others.)
    if config.AUDIO_BRIEFING_ENABLED:
        def build_briefing():
            from . import briefing
            briefing.build_all_variants_for_date(today)
        run_step("audio briefing render (all variants)", build_briefing)

    log.info("Post-digest orchestration finished for %s.", today.isoformat())


# ── T17 same-day digest self-heal ────────────────────────────────────────────
# The 07:00 cron builds the digest once; a bad five minutes there (NVIDIA NIM down +
# OpenRouter 429s, 2026-07-08) left the whole day's pipeline empty until a manual rerun.
# The 20-min ingest cycle reruns the build later in the day when the morning attempt
# left no good digest. Pure decision below; the effecting side lives in main._ingest_job.
DIGEST_RETRY_MAX = 3            # attempts per day (beyond the 07:00 cron)
DIGEST_RETRY_GAP_MIN = 60       # minimum spacing between retries
DIGEST_RETRY_EARLIEST_UTC = (7, 20)  # never before this — give the cron time to finish/fail


def _digest_succeeded(attempts: "list[dict]") -> bool:
    """A day is done once any attempt persisted real content with no error."""
    return any(a.get("error") is None and a.get("has_content") for a in attempts)


def should_retry_digest(
    attempts: "list[dict]",
    now_utc: datetime,
    *,
    max_retries: int = DIGEST_RETRY_MAX,
    gap_min: int = DIGEST_RETRY_GAP_MIN,
    earliest_utc: "tuple[int, int]" = DIGEST_RETRY_EARLIEST_UTC,
) -> bool:
    """Pure T17 decision: should a same-day digest retry run right now?

    `attempts` = today's crypto_digest rows as dicts (created_at, trigger, error,
    has_content), any order — see db.get_digest_attempts. Returns True iff:
      * it is at/after `earliest_utc` (07:20 UTC) — the 07:00 cron has had time to
        succeed or fail, so a missing row means genuine failure, not mid-build;
      * no attempt today produced a good digest (guardrail: never rebuild a good day);
      * fewer than `max_retries` retry attempts have been made today; and
      * the last retry (if any) was ≥ `gap_min` minutes ago. The FIRST retry is gated
        only by the window above, so it can fire at 07:20 right after a 07:00 failure.
    """
    if (now_utc.hour, now_utc.minute) < earliest_utc:
        return False
    if _digest_succeeded(attempts):
        return False
    retries = [a for a in attempts if a.get("trigger") == "retry"]
    if len(retries) >= max_retries:
        return False
    if retries:
        last = util.as_utc(max(r["created_at"] for r in retries))
        if now_utc - last < timedelta(minutes=gap_min):
            return False
    return True


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


# Related-coverage context for the per-bullet analyst (see _generate_one_analysis): how many
# nearby feed items to inject as background, and the cosine-similarity floor below which a
# search_feed hit is treated as too unrelated to be useful (ANN always returns topk, so a floor
# is a coarse prefilter; the real relevance gate is the entity-name anchor in _related_coverage).
_BULLET_CTX_MAX_ITEMS = 4
_BULLET_CTX_MIN_SCORE = 0.35


def _related_coverage(title: str, anchor_names: list[str], own_link: str = "") -> list[str]:
    """Recent feed items that ACTUALLY name one of the bullet's entities — tight background
    for the per-bullet analyst, not just topically-adjacent macro noise.

    Vector-search-by-title alone returns adjacent-but-different items (a niche bullet pulls
    generic BTC/macro coverage that clears the similarity floor), so the resolved entity names
    are the real gate: a hit is kept only if it word-boundary-matches one. No anchor (entity
    detection missed) → empty, which beats injecting scattered context. Fail-silent.
    """
    if not anchor_names:
        return []
    anchor_re = re.compile(r"\b(" + "|".join(re.escape(a) for a in set(anchor_names)) + r")\b", re.I)
    own_link = (own_link or "").strip()
    out: list[str] = []
    try:
        for r in db.search_feed(title, topk=8, days=14):
            if len(out) >= _BULLET_CTX_MAX_ITEMS:
                break
            if float(r.get("score") or 0) < _BULLET_CTX_MIN_SCORE:
                continue
            if own_link and (r.get("link") or "").strip() == own_link:
                continue
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(r.get("content") or ""))).strip()
            if len(text) < 40 or not anchor_re.search(text):
                continue
            when = r.get("pub_date")
            when = str(when.date()) if hasattr(when, "date") else "?"
            out.append(f" - [{when}] {text[:280]}")
    except Exception:
        log.debug("bullet analysis: related-coverage lookup failed (non-fatal)", exc_info=True)
    return out


# Reasoning-model leak guard (2026-07-03 incident, see app/briefing.py for the fuller writeup):
# a reasoning fallback model can emit its own planning/meta-commentary about THIS prompt's
# instructions instead of (or mixed into) the actual analysis, and unlike every other LLM
# write-path in this codebase (threads/podcasts/entity_brief/briefing), this one had NO guard at
# all — the raw completion was stored verbatim. `app/audit.py`'s retrospective scan found this
# already live in 14/347 stored analyses (since 2026-06-04), e.g. "We need to produce 3-4
# sentences of additional context: background..." — a near-verbatim echo of BULLET_ANALYST_SYSTEM
# itself. Phrases are anchored to THIS prompt's specific vocabulary ("context blocks", "must not
# invent", "preserve temporal modality" as a META reference rather than just followed) to keep
# collision risk with genuine 3-4 sentence analyst prose near zero — validated against all 347
# historical stored analyses: 0 false positives, catches all 14 contaminated rows.
_ANALYSIS_LEAK_RE = re.compile(
    r"\bmust not invent\b|\bwe can mention\b|\bwe can include\b|"
    r"\bthe user wants\b|\bcontext blocks?\b|\bpreserve temporal modality\b|"
    r"\bwe need to (?:write|produce|generate|create|extract|summarize|be present)\b|"
    r"\bi need to (?:write|produce|generate|create|extract|summarize)\b|"
    r"\blet me (?:think|write|draft|extract|summarize|parse|note)\b|"
    r"\bi should (?:write|produce|generate|extract|summarize|note|parse)\b",
    re.IGNORECASE,
)


def _clean_analysis(content: str) -> str:
    """Strip a <think> block, then reject the whole completion if it still reads as leaked
    planning/meta-commentary rather than the asked-for analyst prose. Returns '' on a leak (the
    caller then treats it exactly like any other LLM failure for this bullet: log + skip — a
    dropped analysis beats a garbled one reaching the web UI's Analyst Note panel)."""
    text = llm.strip_think(content)
    if _ANALYSIS_LEAK_RE.search(text):
        return ""
    return text


def _generate_one_analysis(bullet: dict) -> dict:
    """Call LLM for a single bullet. Returns the bullet dict enriched with analysis + model_used."""
    title = bullet["title"]
    body = bullet.get("body", "")
    
    # Retrieve ground truth context for matched entities to prevent hallucinations.
    # Two tiers, kept SEPARATE on purpose: live DeFiLlama TVL + Snapshot governance are
    # real DB rows (trustworthy), whereas entity_memory.summary is itself an earlier LLM
    # guess. Presenting the latter as "ground truth" would launder a past hallucination
    # into this prompt, so it is labeled as an unverified hint the model must not quote.
    from . import entities, known_facts
    slugs = entities.detect_entities_in_text(f"{title} {body}")

    # Highest-priority, human-curated ground truth (overrides source spin + prior LLM notes).
    # Matched both on resolved entity slugs and on the raw bullet text (catches an entity the
    # detector missed but the curated trigger term names — e.g. "Arc chain").
    fact_lines = known_facts.facts_for_slugs(slugs)
    for f in known_facts.facts_for_text(f"{title} {body}"):
        if f not in fact_lines:
            fact_lines.append(f)
    # Generic safety net for an un-curated pre-launch entity (feeds the OG hero detail too).
    from . import audit
    for f in audit.prelaunch_warnings(f"{title} {body}"):
        if f not in fact_lines:
            fact_lines.append(f)

    verified_lines: list[str] = []
    note_lines: list[str] = []
    anchor_names: list[str] = []  # entity display names → relevance gate for related coverage
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
            anchor_names.append(name)
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

    # Related recent coverage: feed items that name one of this bullet's entities — real,
    # citable background for "why it matters / what to watch" instead of leaning on model
    # memory (which the grounding rules forbid). Anchored on the resolved entity names so a
    # niche bullet gets tight background or none, never scattered macro noise.
    related_lines = _related_coverage(title, anchor_names, bullet.get("link") or "")

    ctx_blocks: list[str] = []
    if fact_lines:
        from . import known_facts as _kf
        ctx_blocks.append(_kf.block(fact_lines))
    if verified_lines:
        ctx_blocks.append(
            "VERIFIED DATABASE FACTS (live DeFiLlama TVL + Snapshot governance — use as "
            "background; do NOT alter these numbers or invent others):\n"
            + "\n".join(verified_lines)
        )
    if related_lines:
        ctx_blocks.append(
            "RELATED RECENT COVERAGE (other recent feed items near this story — background "
            "context only; do NOT present them as part of today's event, do NOT quote their "
            "numbers as this story's figures, and preserve their tense):\n"
            + "\n".join(related_lines)
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
    analysis = _clean_analysis(content)
    if not analysis:
        raise RuntimeError(f"bullet analysis: reasoning leak or empty completion for {title!r} "
                           f"(model={model})")
    return {**bullet, "analysis": analysis, "model_used": model}


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
