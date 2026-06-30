"""Render a daily digest into a ready-to-post Twitter/X thread.

One thread per digest date: a HOOK tweet (the /api/og card attaches here) followed by ONE
tweet per digest bullet, ordered by importance so the thread mirrors the OG card's ranking.

Design — reuse, don't re-derive:
  * Bullets + their source links come from the digest HTML (``digest._parse_digest_bullets``).
  * Each bullet's grounded expansion (``analysis``) + ``importance_score`` come straight from
    ``digest_bullet_analysis`` — already computed by the post-digest bullet-analysis step.
  * A SINGLE LLM call compresses (headline + summary + analyst note) into tweet voice. It is a
    pure REWRITE of already-grounded text under a strict "no new facts" rule, so the thread
    inherits the bullet-analysis grounding and never re-fetches TVL/governance.
  * URLs are NEVER emitted by the model — the verbatim source link is attached in code, so a
    thread can't fabricate or mangle a link (the #1 grounding risk).

Resilience (mirrors the rest of the pipeline): best-effort, never breaks the digest. If the
LLM call fails or a per-bullet tweet is missing/over-budget, that bullet falls back to a
deterministic clip of its headline+summary — so EVERY bullet always has a usable tweet.
"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import date as date_t, datetime, timezone

from . import config, db, llm, prompts

log = logging.getLogger(__name__)

# Reasoning-model leak guard (same failure mode handled in podcasts/entity_brief).
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)

# Char budgets. X's hard limit is 280; a wrapped t.co link costs 23 chars + 1 space, so a
# body tweet that gets a link appended must leave room for it.
HOOK_MAX = 270          # no link on the hook (the OG image attaches there)
TWEET_TEXT_MAX = 255    # full tweet text incl. @tags; + " " + 23-char t.co link = 279 ≤ 280
OG_CARD_BULLETS = 5     # the /api/og card shows the top-N (it clamps 1–5 anyway)
MAX_TAGS = 3            # cap entity @handles offered per tweet (avoid tag spam)
HORYON_HANDLE = "@Horyonhq"  # the official Horyon X account

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_HANDLE_TOKEN_RE = re.compile(r"@(\w{1,20})")  # an @handle token in model output

# Dash de-slop: em/en dashes and spaced double/single hyphens used as sentence
# punctuation are the #1 "this was written by an AI" tell. Strip them even when
# the model ignores the prompt. A dash BETWEEN DIGITS is a numeric range → keep a
# plain hyphen; ordinary hyphenated words (on-chain) have no surrounding spaces and
# are untouched.
_DASH_NUM_RE = re.compile(r"(?<=\d)\s*[—–]\s*(?=\d)")
_DASH_SEP_RE = re.compile(r"\s*[—–]\s*|\s+--+\s+|\s+-\s+")


def _dedash(text: str) -> str:
    text = _DASH_NUM_RE.sub("-", text or "")
    text = _DASH_SEP_RE.sub(", ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_think(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    text = _THINK_OPEN_RE.sub("", text)
    return text.strip()


def _clip(text: str, limit: int) -> str:
    """Word-boundary clip to ``limit`` chars, no visible ellipsis (mirrors the OG card)."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,.;:—–-")


def _fit(text: str, limit: int) -> str:
    """Trim to whole sentences within ``limit`` — never leave a dangling fragment.

    A tweet that trails off mid-thought ("...Watch devnet metrics for") reads as broken, so
    keep as many COMPLETE sentences as fit; only if the very first sentence already overflows
    do we fall back to a word-boundary clip.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    out = ""
    for s in _SENT_SPLIT.split(text):
        cand = f"{out} {s}".strip() if out else s
        if len(cand) <= limit:
            out = cand
        else:
            break
    return out or _clip(text, limit)


def _fit_whole(text: str, limit: int) -> str:
    """Whole-sentence fit that returns '' when nothing fits — NEVER a mid-sentence clip.

    Used for the secondary why-it-matters line: a clipped analyst line ("...boosting Pendle's")
    reads broken, and an absent why is acceptable by design, so we drop rather than clip.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= limit:
        return text
    out = ""
    for s in _SENT_SPLIT.split(text):
        cand = f"{out} {s}".strip() if out else s
        if len(cand) <= limit:
            out = cand
        else:
            break
    return out  # '' if even the first sentence overflows → caller drops the why


def _fallback_tweet(bullet: dict) -> str:
    """Deterministic, still-substantive tweet when the LLM didn't produce a usable one —
    leans on the already-grounded analysis (falls back to the bullet body)."""
    title = bullet["title"].strip()
    detail = (bullet.get("analysis") or bullet.get("body") or "").strip()
    return _fit(f"{title}. {detail}" if detail else title, TWEET_TEXT_MAX)


_TICKER_RE = re.compile(r"[A-Z0-9]{2,6}")


def _find_surface(text: str, row: dict) -> str:
    """The exact substring (original case) by which this entity appears in the bullet —
    longest matching name/alias wins, so a ticker mention ('USDC') is captured as written
    even though the entity name is the issuer ('Circle')."""
    cands = [row.get("name") or ""] + [a for a in (row.get("aliases") or []) if a]
    cands = [c for c in cands if c and not c.startswith("@")]
    cands.sort(key=len, reverse=True)
    for c in cands:
        m = re.search(rf"(?<!\w){re.escape(c)}(?!\w)", text, re.I)
        if m:
            return m.group(0)
    return row.get("name") or ""


def _is_asset_surface(surface: str, name: str) -> bool:
    """True when the entity is mentioned by an asset TICKER (USDC, USDT, DAI…) that differs
    from its issuer name (Circle, Tether). Such tags KEEP the ticker and append the handle
    after it ('USDC @circle') rather than replacing the ticker with the handle."""
    s = (surface or "").strip()
    return bool(_TICKER_RE.fullmatch(s)) and s.lower() != (name or "").lower()


def _entity_tags(title: str, body: str) -> list[dict]:
    """[{name, handle, surface, asset, in_title}] for entities mentioned in the bullet that
    have a Twitter handle — primary (title) entities first, capped at MAX_TAGS. Sourced from
    entity_memory, never model-invented (mirrors how source links are code-attached)."""
    from . import entities  # lazy: heavy module, only needed when rendering

    text = f"{title} {body}"
    try:
        slugs = entities.detect_entities_in_text(text)
    except Exception:
        log.debug("thread: entity detection failed", exc_info=True)
        return []
    if not slugs:
        return []
    rows = {r["slug"]: r for r in db.get_entities_by_slugs(slugs)}

    def _in_title(term: str) -> bool:
        return bool(term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", title, re.I))

    cand: list[dict] = []
    for slug in slugs:
        r = rows.get(slug)
        if not r:
            continue
        handle = (r.get("twitter_handle") or "").strip()
        name = (r.get("name") or "").strip()
        if not handle or not name:
            continue
        surface = _find_surface(text, r)
        cand.append({
            "name": name,
            "handle": "@" + handle.lstrip("@"),
            "surface": surface,
            "asset": _is_asset_surface(surface, name),
            "in_title": _in_title(name) or _in_title(surface),
        })
    cand.sort(key=lambda c: not c["in_title"])  # title entities first (stable within group)

    out: list[dict] = []
    seen: set[str] = set()
    for c in cand:
        if c["handle"].lower() in seen:
            continue
        seen.add(c["handle"].lower())
        out.append(c)
        if len(out) >= MAX_TAGS:
            break
    return out


def _append_asset_tags(text: str, assets: list[dict]) -> str:
    """Keep each asset ticker and place its issuer @handle right after the first occurrence
    ('USDC @circle'), never replacing the ticker. Best-effort + budget-aware; if the ticker
    isn't in the tweet (model dropped it) we don't force it in."""
    for a in assets:
        surface, handle = a.get("surface", ""), a["handle"]
        if not surface:
            continue
        # `[\w/-]` boundaries: skip a ticker that is part of a pair/compound
        # ("USDC-cbMEGA", "PT-USD3/USDC") — inserting a handle there splits the pair.
        pos = None
        for m in re.finditer(rf"(?<![\w/-]){re.escape(surface)}(?![\w/-])", text, re.I):
            tail = text[m.end():]
            if re.match(rf"\s*{re.escape(handle)}\b", tail, re.I):
                pos = None  # already tagged right after the ticker — leave it
                break
            # Don't split a product name ("USDC Vault", "USDC Market") — a Capitalized word
            # right after the ticker means it's a compound; try a later, standalone occurrence.
            if re.match(r"\s+[A-Z][a-z]", tail):
                continue
            pos = m.end()
            break
        if pos is None:
            continue
        candidate = f"{text[:pos]} {handle}{text[pos:]}"
        if len(candidate) <= TWEET_TEXT_MAX:
            text = candidate
    return text


def _validate_handles(text: str, allowed: set[str]) -> str:
    """Strip any @handle the model produced that isn't in the allow-list (keep the bare word).
    Handles are DB truth — the model must never coin or misattribute one."""
    return _HANDLE_TOKEN_RE.sub(
        lambda m: m.group(0) if m.group(0).lower() in allowed else m.group(1), text)


def _finalize_tweet(text: str, inline_tags: list[dict], allowed: set[str],
                    limit: int = TWEET_TEXT_MAX) -> str:
    """Validate model @handles against the allow-list, guarantee the primary inline account is
    tagged even if the model skipped it, and fit to ``limit`` chars. (Asset tickers are tagged
    separately, after this, by ``_append_asset_tags``.)"""
    text = _dedash(text)
    text = _validate_handles(text, allowed)
    if inline_tags and not _HANDLE_TOKEN_RE.search(text):
        primary = inline_tags[0]["handle"]
        text = f"{_fit(text, limit - len(primary) - 1)} {primary}".strip()
    return _fit(text, limit)


# ── Intelligence Brief composition ─────────────────────────────────────────────
# Each tweet is a ranked micro-brief, not a flat headline relay:
#   #N · M sources        ← curation + corroboration signal (rank = importance order)
#   <what happened>       ← the grounded development + numbers (carries entity @tags)
#   Why it matters: <…>   ← ONE grounded analyst sentence (the differentiator)
WHY_LABEL = "Why it matters: "
DEV_MAX = 170      # the development (the actual news) gets budget PRIORITY — must read whole
WHY_MAX = 100      # the analyst line takes whatever room remains under this cap
WHY_MIN = 32       # below this an analyst line reads as a stub — drop it (empty beats clipped)
WHY_RESERVE = 72   # room held back from the development so a why-line usually survives WHOLE

# Generic-filler "why" lines the prompt already bans but the model still emits. We enforce
# the same rule in code: an empty why beats a bland one (stated design philosophy). A why is
# only dropped when it is BOTH generic-filler AND carries no grounding signal (no number, no
# @handle) to redeem it — so a specific takeaway that happens to contain a banned word survives.
_BLAND_WHY_RE = re.compile(
    r"\b("
    r"improv\w+ efficiency|boost\w* liquidity|add\w* liquidity|"
    r"increas\w+ capital efficiency|enhanc\w+|driv\w+ (growth|adoption)|"
    r"affect\w+ sentiment|competitive landscape|"
    r"signal\w* (broader|grow\w+|strong\w*|renewed|continued|increas\w+) "
    r"(adoption|demand|interest|competition|momentum)|"
    r"expand\w* (low-risk )?(yield )?strateg\w+|position\w* \w+ for|"
    r"could (draw|attract|drive|boost)|underscor\w+|highlight\w*"
    r")\b", re.I)


def _clean_why(why: str) -> str:
    """Enforce the prompt's no-generic-filler rule in code. Returns '' (drop the why) when the
    line is generic boilerplate with no grounding signal — an empty why beats a bland one."""
    why = (why or "").strip()
    if not why:
        return ""
    redeemed = bool(re.search(r"\d", why) or "@" in why)
    if _BLAND_WHY_RE.search(why) and not redeemed:
        return ""
    return why


def _rank_line(n: int, source_count) -> str:
    """Header line: rank within the day + source corroboration count (count shown only when
    ≥2 distinct sources — a single source isn't a corroboration signal, mirrors the web badge)."""
    sc = source_count or 0
    return f"#{n} · {sc} sources" if sc >= 2 else f"#{n}"


def _compose_brief_tweet(n: int, what: str, why: str, source_count,
                         inline: list[dict], assets: list[dict], allowed: set[str]) -> str:
    """Assemble one Intelligence-Brief tweet (rank header · grounded development · why-it-matters).

    The DEVELOPMENT is budgeted FIRST and prefers whole sentences, so the actual news never
    trails off mid-thought; the why-it-matters line then takes whatever room remains and is
    dropped entirely when too little is left (an empty why beats a clipped or stub one). The
    development carries the entity @tags; the why line is validated + de-slopped but tag-free."""
    header = _rank_line(n, source_count)
    base = TWEET_TEXT_MAX - len(header) - 1  # minus the header + its newline
    why = _clean_why(_dedash(_validate_handles(why or "", allowed)))
    # 1) Development first (priority) but HOLD BACK room for the why so it survives whole:
    #    validate handles, guarantee the primary tag, asset-tag, then sentence-fit.
    reserve = (len(WHY_LABEL) + WHY_RESERVE) if why else 0
    dev_budget = min(base - reserve, DEV_MAX)
    what = _finalize_tweet(what, inline, allowed, limit=dev_budget)
    what = _fit(_append_asset_tags(what, assets), dev_budget)
    # 2) Why-it-matters takes the remaining room — fit to WHOLE sentences, dropped (never
    #    clipped mid-thought) if even one sentence won't fit what's left.
    why_block = ""
    if why:
        remaining = base - len(what) - 1 - len(WHY_LABEL)  # minus the why newline + label
        fitted = _fit_whole(why, min(remaining, WHY_MAX)) if remaining >= WHY_MIN else ""
        if len(fitted) >= WHY_MIN:
            why_block = f"\n{WHY_LABEL}{fitted}"
    return f"{header}\n{what}{why_block}"


def _masthead_date(d: date_t) -> str:
    """Brand-consistent dated header label, mirrors the OG card ('WED · JUN 17, 2026')."""
    return d.strftime("%a · %b %d, %Y").replace(" 0", " ").upper()


def _compose_hook(digest_date: date_t, llm_hook: str, count: int) -> str:
    """Templated hook tweet: a dated Horyon masthead + the factual throughline + a ranked cue.

    A fixed header line guarantees every thread opens the same way (date + brand) regardless of
    the model, which is what makes a recurring daily thread recognisable on the timeline. The cue
    states the curated count + ranking so a reader knows the thread is short and editorially picked.
    """
    header = f"🦅 HORYON DAILY · {_masthead_date(digest_date)}"
    cue = f"Top {count} signals, ranked 👇" if count else "Today's signals 👇"
    body_budget = HOOK_MAX - len(header) - len(cue) - 4  # 2× "\n\n" joins
    body = _fit(llm_hook, body_budget) if llm_hook else ""
    parts = [header, body, cue] if body else [header, cue]
    return "\n\n".join(parts)


def _build_closer(digest_date: date_t) -> str:
    """Closing tweet: explain what Horyon is (so a new reader gets the value) + CTA + the handle."""
    return (
        "That's today's onchain intel.\n\n"
        "Horyon scans ~100 crypto sources every 20 min and ranks what actually matters, "
        "with analyst notes on every signal.\n\n"
        f"Full feed & live charts → {config.PUBLIC_BASE_URL}\n"
        f"Follow {HORYON_HANDLE} for the daily thread."
    )


def _ordered_bullets(digest_date: date_t) -> list[dict]:
    """Merge digest-HTML bullets (carry the source link) with their stored analysis rows
    (carry analysis + importance_score), ordered for the thread (importance desc, stable)."""
    from .digest import _parse_digest_bullets  # lazy: avoids an import cycle with digest.py

    digest = db.get_digest(digest_date)
    if not digest or not digest.get("content"):
        return []
    parsed = _parse_digest_bullets(digest["content"])
    if not parsed:
        return []
    # Analyses are keyed on the SAME parser's title, so an exact-title join is reliable.
    rows = {r["title"]: r for r in db.get_bullet_analyses_full(digest_date)}
    merged = []
    for idx, b in enumerate(parsed):
        row = rows.get(b["title"], {})
        merged.append({
            "title": b["title"],
            "body": b.get("body", ""),
            "link": b.get("link"),
            "analysis": row.get("analysis", ""),
            "importance_score": row.get("importance_score"),
            "source_count": row.get("source_count"),  # corroboration signal (Intelligence Brief header)
            "_idx": idx,  # stable tiebreak → preserves digest order within equal scores
        })
    # Importance desc, NULLs last, stable on original order (matches /api/og ranking).
    merged.sort(key=lambda m: (m["importance_score"] is None,
                               -(m["importance_score"] or 0), m["_idx"]))
    return merged


def _generate_tweets(date_str: str, bullets: list[dict]) -> tuple[str, dict[int, dict], str]:
    """One LLM call → (hook, {1-based index: {text, why}}, model_used). Empty on failure."""
    # Only INLINE entities are offered to the model; asset tickers are tagged in code after
    # generation so the ticker is kept (e.g. "USDC @circle") instead of being replaced.
    from . import entities, known_facts, audit  # lazy: heavy modules, only needed when rendering
    signals = []
    for b in bullets:
        text = f"{b['title']} {b['body']}"
        facts = known_facts.facts_for_slugs(entities.detect_entities_in_text(text))
        for f in known_facts.facts_for_text(text):
            if f not in facts:
                facts.append(f)
        # Safety net for the NEXT Arc: a generic modality warning for any entity our own
        # memory marks pre-launch/testnet, even if it's not yet a curated known_fact.
        for f in audit.prelaunch_warnings(text):
            if f not in facts:
                facts.append(f)
        signals.append({
            "title": b["title"], "body": b["body"], "analysis": b["analysis"],
            "accounts": [(a["name"], a["handle"])
                         for a in b.get("accounts", []) if not a["asset"]],
            "facts": facts,
        })
    user = prompts.build_thread_user(date_str, signals)
    try:
        content, model = llm.complete(prompts.THREAD_SYSTEM, user, max_tokens=1200,
                                      temperature=0.45, json_mode=True)
    except Exception:
        log.warning("thread: LLM call failed; using deterministic fallback", exc_info=True)
        return "", {}, ""

    content = _strip_think(content)
    try:
        data = llm.parse_json_loose(content)
    except Exception:
        log.warning("thread: could not parse LLM JSON; deterministic fallback", exc_info=True)
        return "", {}, model
    if not isinstance(data, dict):
        return "", {}, model

    hook = _fit(str(data.get("hook", "")), HOOK_MAX)
    by_idx: dict[int, dict] = {}
    items = data.get("tweets", [])
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("i"))
        except (TypeError, ValueError):
            continue
        text = _strip_think(str(it.get("text", "")))
        if text:  # validated + fit later in _compose_brief_tweet (needs the allow-list)
            by_idx[i] = {"text": text, "why": _strip_think(str(it.get("why", "")))}
    return hook, by_idx, model


def build_thread_for_date(digest_date: date_t | None = None, persist: bool = True) -> dict | None:
    """Build (and optionally store) the Twitter thread for a digest date.

    Returns the thread dict, or None when the date has no digest bullets.
    """
    digest_date = digest_date or datetime.now(timezone.utc).date()
    bullets = _ordered_bullets(digest_date)
    if not bullets:
        log.info("thread: no bullets for %s — skipping", digest_date)
        return None
    # Intelligence Brief: ship the curated TOP-N only (mirrors the OG card) — signal over
    # volume. The lower-ranked digest bullets are deliberately cut, not relayed.
    bullets = bullets[:OG_CARD_BULLETS]

    # Resolve entity Twitter handles per bullet (DB-sourced — never model-invented).
    for b in bullets:
        b["accounts"] = _entity_tags(b["title"], b["body"])

    raw_hook, by_idx, model = _generate_tweets(str(digest_date), bullets)
    # Strip any handle the model invented in the hook (validate against ALL bullets' allow-lists).
    allowed_all = {a["handle"].lower() for b in bullets for a in b["accounts"]}
    raw_hook = _dedash(_validate_handles(raw_hook, allowed_all))
    # Wrap the (factual) throughline in the dated Horyon masthead template.
    hook = _compose_hook(digest_date, raw_hook, len(bullets))

    # PRE-PUBLISH GATE (the thread is the only outward-facing, auto-posted, irreversible
    # surface). A composed tweet/hook that asserts a pre-launch/testnet entity as already
    # LIVE (the Arc failure mode) is caught by the SAME modality check the auditor uses; the
    # tweet falls back to its deterministic clip, and if that's still unsafe the whole thread
    # is stored 'blocked' so the external poster (which only ships 'pending') never sends it.
    from . import audit  # lazy: pulls db/entities; only needed at render time
    gate_pats = audit.compile_prelaunch_patterns()
    blocking: list[str] = []
    for slug, sent in audit.modality_violations(hook, gate_pats):
        blocking.append(f"hook→{slug}: {sent}")

    tweets = []
    for n, b in enumerate(bullets, 1):  # 1-based numbering = the signal's rank
        gen = by_idx.get(n) or {}
        why = gen.get("why", "")
        inline = [a for a in b["accounts"] if not a["asset"]]
        assets = [a for a in b["accounts"] if a["asset"]]
        allowed = {a["handle"].lower() for a in b["accounts"]}
        text = _compose_brief_tweet(n, gen.get("text") or _fallback_tweet(b), why,
                                    b.get("source_count"), inline, assets, allowed)
        if audit.modality_violations(text, gate_pats):
            # LLM tweet tripped → retry with the deterministic fallback clip (no 'why').
            fb = _compose_brief_tweet(n, _fallback_tweet(b), "", b.get("source_count"),
                                      inline, assets, allowed)
            fb_hits = audit.modality_violations(fb, gate_pats)
            text = fb
            if fb_hits:  # even the grounded fallback is unsafe → block the whole thread
                blocking.append(f"tweet {n}→{fb_hits[0][0]}: {fb_hits[0][1]}")
            else:
                log.warning("thread gate: tweet %d asserted a pre-launch entity as live → "
                            "used deterministic fallback (%s)", n, b["title"][:60])
        tweets.append({
            "title": b["title"],
            "text": text,
            "link": b["link"],
            "importance_score": b["importance_score"],
            "source_count": b.get("source_count"),
        })

    n_card = min(OG_CARD_BULLETS, len(tweets))
    og_url = f"{config.PUBLIC_BASE_URL}/api/og?date={digest_date}&type=daily&bullets={n_card}"
    cta = _build_closer(digest_date)

    status = "blocked" if blocking else "pending"
    if blocking:
        log.error("thread gate: %s BLOCKED (status=blocked, not posted) — %d unresolved "
                  "modality violation(s): %s", digest_date, len(blocking), " | ".join(blocking))

    thread = {
        "digest_date": digest_date,
        "hook": hook,
        "tweets": tweets,
        "cta": cta,
        "og_image_url": og_url,
        "model_used": model,
        "status": status,
    }
    if persist:
        db.upsert_thread(**thread)
        log.info("thread: stored %d tweet(s) for %s (status=%s)", len(tweets), digest_date, status)
    return thread


def _print(thread: dict) -> None:
    print(f"\n🧵 HOOK  [image: {thread['og_image_url']}]\n{thread['hook']}\n")
    for i, tw in enumerate(thread["tweets"], 1):
        link = tw["link"] or ""
        score = tw["importance_score"]
        tag = f" ({score})" if score is not None else ""
        print(f"{i}.{tag} {tw['text']} {link}".rstrip())
    print(f"\n{thread['cta']}\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Render daily digest(s) into ready-to-post Twitter threads.")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="build for one date (default: today)")
    ap.add_argument("--no-persist", action="store_true", help="print only, don't store")
    ap.add_argument("--backfill", action="store_true",
                    help="build for every digest date that has analyses but no thread")
    args = ap.parse_args()

    persist = not args.no_persist
    if args.backfill:
        dates = db.get_digest_dates_without_thread()
        print(f"Backfilling threads for {len(dates)} date(s)…")
        for d in dates:
            t = build_thread_for_date(d, persist=persist)
            print(f"  {d}: {'skipped (no bullets)' if t is None else str(len(t['tweets'])) + ' tweets'}")
        print("Done.")
    else:
        d = date_t.fromisoformat(args.date) if args.date else None
        t = build_thread_for_date(d, persist=persist)
        if t is None:
            print("No thread — no digest bullets for that date.")
        else:
            _print(t)
