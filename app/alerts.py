"""Breaking-news Telegram alerts — deterministic, NO LLM.

Off the 20-min ingest cycle (app/main.py _maybe_send_alerts), freshly-ingested feed items are
scored with the SAME deterministic signals as the daily digest (app/scoring.score_item); the
alert-worthy ones are pushed to Telegram immediately, quoting the item's own title + source +
link. Because nothing is model-generated, there is no hallucination surface (and no modality
gate beyond telegram_html.sanitize). Telegram-only — alerts are not surfaced on the web.

Decision (balanced): a security event (hack/exploit/…) with a large $ magnitude is a HARD
TRIGGER and alerts regardless of score; otherwise an item must clear ALERT_MIN_SCORE AND be
corroborated (≥ ALERT_MIN_SOURCES distinct credible sources, or one premium/Tier-1 source).
Deduped against alerts_sent (never re-alert a story as more outlets pick it up) and today's
digest coverage, and rate-capped per hour/day.

CLI:  python -m app.alerts [--dry-run]   # print what WOULD alert; no send, no DB write
"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import datetime, timezone

from . import config, db, scoring, util
from .digest import _build_dedup_context
from .telegram_html import sanitize

log = logging.getLogger(__name__)

_CATEGORY_LABEL = {
    "security":    "🚨 Security",
    "funding":     "💰 Funding",
    "governance":  "🗳 Governance",
    "launch":      "🚀 Launch",
    "partnership": "🤝 Partnership",
}

_MAX_TITLE = 180  # defensive truncation for the alert headline

# Topical exclusions — the themes the digest HARD-DISCARDs (TradFi/ETF, treasury, price/TA,
# regulation, quantum). A breaking ALERT should be an onchain / security / funding event, not
# market commentary, so an item whose text hits these is dropped even if it carries an event
# keyword. Deterministic mirror of the digest prompt's HARD DISCARD list. Multi-word phrases
# match across whitespace; single words are word-boundary matched.
_EXCLUDE_TERMS = [
    "etf", "etfs", "futures", "cme", "cftc", "options", "derivative", "derivatives",
    "blackrock", "fidelity", "microstrategy", "treasury", "balance sheet",
    "resistance", "support level", "fibonacci", "retracement", "moving average",
    "price target", "rally", "bearish", "bullish", "all-time high", "market cap",
    "lawsuit", "regulation", "regulatory", "quantum",
]
_EXCLUDE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in _EXCLUDE_TERMS) + r")\b",
    re.IGNORECASE,
)


def _quiet_hours() -> set[int]:
    out: set[int] = set()
    for tok in (config.ALERT_QUIET_HOURS_UTC or "").split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.add(int(tok) % 24)
    return out


def _source_label(link: str) -> str:
    """Compact human label for a source: '@handle' for Twitter, else the bare domain."""
    return scoring.get_source_key(link) or "source"


def _same_story(w1: set, w2: set) -> bool:
    """Aggressive same-story test for alert dedup. The digest's is_semantic_duplicate (0.6
    Jaccard) is tuned conservative — it relies on the LLM to merge same-story-different-headline
    items — but a deterministic alert has no such merge step, so three outlets covering one hack
    would otherwise fire three alerts. This adds a looser fallback: ≥3 shared significant words
    (unless the two name DIFFERENT chains — that's two deployments, not one story). Alerts prefer
    NOT re-sending over catching every angle, so erring toward merge is the right trade."""
    if not w1 or not w2:
        return False
    if scoring.is_semantic_duplicate(w1, w2):
        return True
    c1, c2 = w1 & scoring._CHAIN_WORDS, w2 & scoring._CHAIN_WORDS
    if c1 and c2 and c1 != c2:
        return False
    return len(w1 & w2) >= 3


def _covered_word_sets_today(day) -> list[set]:
    """Title word-sets already covered TODAY (morning digest + intraday updates) — an item
    that just re-reports a story already in today's coverage is not a fresh alert."""
    rows: list[tuple] = []
    morning = db.get_digest(day)
    if morning and morning.get("content"):
        rows.append((day, morning["content"]))
    for upd in db.get_digest_updates(day):
        rows.append((day, upd["content"]))
    _, covered = _build_dedup_context(rows)
    return [w for w in (scoring.get_title_words(b["title"]) for b in covered if b.get("title")) if w]


def _passes(sc: dict) -> tuple[bool, bool]:
    """(should_alert, is_hard_trigger) for a scored item. Hard trigger = a security event with
    a ≥ ALERT_HARD_AMOUNT magnitude (real theft is never gated on corroboration/score).

    The non-hard path requires a genuine EVENT CATEGORY (security/funding/launch/governance),
    not just a stray $ figure — a price post with a '$12B market cap' number but no event is
    not news. It must also clear the score floor AND be corroborated."""
    hard = sc.get("category") == "security" and (sc.get("amount_usd") or 0) >= config.ALERT_HARD_AMOUNT
    if hard:
        return True, True
    if not sc.get("category"):
        return False, False
    if (sc.get("score") or 0) < config.ALERT_MIN_SCORE:
        return False, False
    corroborated = ((sc.get("source_count") or 0) >= config.ALERT_MIN_SOURCES
                    or (sc.get("max_credibility") or 0) >= config.ALERT_PREMIUM_CREDIBILITY)
    return corroborated, False


def find_alertable(now: datetime | None = None) -> list[dict]:
    """Score the last cycle's unalerted feed items and return the ones worth alerting, ranked
    (hard triggers first, then by score), capped to the remaining hourly/daily budget. Pure
    read — never sends or persists. Best-effort: any failure returns []."""
    now = now or datetime.now(timezone.utc)
    if not config.ALERTS_ENABLED:
        return []
    if now.hour in _quiet_hours():
        return []

    # Remaining budget under the per-hour and per-day caps.
    try:
        budget = min(config.ALERT_MAX_PER_HOUR - db.count_alerts_since(60),
                     config.ALERT_MAX_PER_DAY - db.count_alerts_since(24 * 60))
    except Exception:
        log.warning("alerts: rate-cap lookup failed", exc_info=True)
        return []
    if budget <= 0:
        return []

    try:
        items = db.get_recent_unalerted_items(config.ALERT_SCAN_MINUTES)
    except Exception:
        log.warning("alerts: could not load recent items", exc_info=True)
        return []
    if not items:
        return []

    day = now.date()
    try:
        ref = scoring.build_ref_data(day)
    except Exception:
        log.warning("alerts: reference data load failed", exc_info=True)
        return []

    # Dedup corpora: stories alerted recently + stories already in today's digest coverage.
    try:
        recent = db.get_recent_alerts(config.ALERT_DEDUP_HOURS)
        alerted_sets = [w for w in (scoring.get_title_words(a.get("title") or "") for a in recent) if w]
        covered_sets = _covered_word_sets_today(day)
    except Exception:
        log.debug("alerts: dedup corpus load failed (non-fatal)", exc_info=True)
        alerted_sets, covered_sets = [], []

    candidates: list[dict] = []
    batch_sets: list[set] = []
    for it in items:
        # News-source items only by default: they carry real editorial headlines, one topic
        # each — tweets put the whole post in `title` and score noisily.
        if config.ALERT_NEWS_ONLY and (it.get("source_type") or "") != "news":
            continue
        title = (it.get("title") or "").strip()
        text = f"{title} {it.get('content') or ''}"
        # Every alertable item needs a genuine EVENT category (a hard trigger also requires the
        # security category), so pre-gate on the cheap no-DB signal — this both matches _passes
        # and avoids a corroboration query per non-eventful item.
        cat0, _amt0 = scoring.prescreen_category_amount(text)
        if not cat0:
            continue
        # Topical exclusion: TradFi/ETF/treasury/price-TA/regulation/quantum are not alerts.
        # NEVER applied to security events — "DAO treasury drained in $60M exploit" contains
        # "treasury" but is exactly the story alerts exist for; the exclude list is for market
        # commentary, not incident reports.
        if cat0 != "security" and _EXCLUDE_RE.search(text):
            continue

        words = scoring.get_title_words(title or (it.get("content") or "")[:120])
        # Skip stories already alerted / already in today's coverage / already in this batch.
        if words and any(_same_story(words, cw)
                         for cw in (alerted_sets + covered_sets + batch_sets)):
            continue

        try:
            sc = scoring.score_item(it, ref, day, ref_time=now)
        except Exception:
            log.debug("alerts: scoring failed for %r", title, exc_info=True)
            continue
        ok, hard = _passes(sc)
        if not ok:
            continue

        if words:
            batch_sets.append(words)
        candidates.append({
            "link": it.get("link") or "",
            "title": title,
            "category": sc.get("category") or "",
            "score": sc.get("score") or 0,
            "source_count": sc.get("source_count") or 0,
            "amount_usd": sc.get("amount_usd") or 0.0,
            "source_key": scoring.get_source_key(it.get("link") or ""),
            "_hard": hard,
        })

    candidates.sort(key=lambda c: (c["_hard"], c["score"]), reverse=True)
    return candidates[:budget]


def format_alert(a: dict) -> str:
    """Render one alert as Telegram HTML. Deterministic — quotes the item's own title + source;
    the ONLY link is the item's own URL, so there is no injected/invented URL to guard."""
    head = _CATEGORY_LABEL.get(a.get("category") or "", "📣 Breaking")
    amt = a.get("amount_usd") or 0
    if amt >= 1_000_000:
        head += f" · {util.fmt_usd(amt)}"

    raw_title = util.decode_entities(a.get("title") or "").strip()
    if len(raw_title) > _MAX_TITLE:
        raw_title = raw_title[:_MAX_TITLE].rstrip() + "…"
    title = sanitize(raw_title) or "(untitled)"
    # Attribute-safe href: a quote in a (hostile) link must not break out of href="…".
    link = (a.get("link") or "").replace('"', "%22")
    src = sanitize(_source_label(link))
    n = a.get("source_count") or 0
    tail = f" · {n} sources" if n >= 2 else ""
    loc = f'<a href="{link}">{src}{tail}</a>' if link else f"{src}{tail}"
    return f"{head}\n<b>{title}</b>\n{loc}"


def mark_alerted(alerts: list[dict]) -> int:
    """Record alerts in alerts_sent (dedup + rate cap). Returns rows inserted."""
    return db.insert_alerts_sent(alerts)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Find breaking-news alert candidates.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what WOULD alert; do not send or record (default)")
    ap.add_argument("--record", action="store_true",
                    help="also write alerts_sent rows (still does not send Telegram)")
    args = ap.parse_args()

    found = find_alertable()
    if not found:
        print("(no alert-worthy items this cycle)")
    else:
        for a in found:
            hard = " [HARD]" if a.get("_hard") else ""
            print(f"--- score={a['score']} src={a['source_count']} "
                  f"cat={a['category'] or '-'}{hard}")
            print(format_alert(a))
            print()
        if args.record and not args.dry_run:
            n = mark_alerted(found)
            print(f"[recorded {n} row(s) in alerts_sent]")
