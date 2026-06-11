"""Importance scoring for daily-digest bullets (0–100) + corroborating source count.

Fully deterministic — NO LLM. (The two LLM calibration/ranking passes were removed:
they added cost + free-tier rate-limit risk for little signal over the Python signals.)

Pipeline (per bullet):
  1. Python signals (deterministic, summed then capped at 100):
       s1 corroboration: SUM of source CREDIBILITY weights, not a raw count  (0–25)
       s2 financial magnitude                                                (0–20)
       s3 appearance velocity                                                (0–15)
       s4 entity weight (TVL or 30d mention count)                           (0–20)
       s5 semantic criticality (keyword buckets)                             (0–15)
       s6 novelty vs last 7 days of digests (semantic, Jaccard)              (0– 5)
  2. Credibility penalty: ×0.5 when ONLY Tier-3 (clickbait) sources reported it.
  3. Temporal decay (story age at digest time).

Source credibility (``FEED_CREDIBILITY``): Tier 1 trusted = 1.2, Tier 2 default = 1.0,
Tier 3 clickbait = 0.4. Keyed by domain or Twitter handle (``get_source_key``).

Design notes:
  - No raw psycopg2 connection is passed in — all queries go through ``db`` helpers
    (project convention: "all CRUD lives in db.py").
  - Feed/decay windows are anchored to the *digest date*, not ``now()``, so the
    same code produces sane scores during live runs and historical backfill.
  - ``score_breakdown`` still carries ``llm_adjustment``/``position_bonus`` keys
    (always 0) for backward-compatibility with the stored schema.

Every step is best-effort: any failure leaves a bullet with score=None and never
propagates — digest generation must not break on scoring.
"""
from __future__ import annotations

import logging
import re
from datetime import date as date_t, datetime, time, timezone
from urllib.parse import urlparse

from . import db, llm, prompts

log = logging.getLogger(__name__)

# ── Signal 2: financial magnitude ──────────────────────────────────────────
_AMOUNT_RE = re.compile(r"\$\s*([\d,.]+)\s*(b|m|k|billion|million|thousand)?", re.IGNORECASE)
_MULT = {
    "b": 1e9, "billion": 1e9,
    "m": 1e6, "million": 1e6,
    "k": 1e3, "thousand": 1e3,
}

# ── Signal 5: semantic criticality keyword buckets (max bucket, not sum) ────
_KEYWORDS = {
    15: ["hack", "exploit", "drain", "blacklist", "freeze", "rug", "breach", "attack"],
    11: ["vulnerability", "emergency", "pause", "governance", "vote", "proposal", "shutdown"],
    7:  ["mainnet", "upgrade", "v2", "v3", "launch", "audit", "migration", "deploy"],
    3:  ["partnership", "integration", "update", "support", "adds", "enables"],
}
_KEYWORD_PATTERNS = {
    score: [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in words]
    for score, words in _KEYWORDS.items()
}

# ── Dedup / novelty (Jaccard-based semantic comparison) ────────────────────
_EMOJI_RE = re.compile(r"[^\w\s]", re.UNICODE)
_DEDUP_STOP = frozenset({
    "a", "an", "the", "on", "in", "to", "of", "for", "and", "or", "with", "by",
    "at", "is", "are", "has", "was", "from", "its", "into", "new", "live", "goes", "s", "d",
    "today", "now", "week", "month", "year", "yesterday", "tomorrow", "tonight"
})
_PREFIX_RE = re.compile(
    r"^(breaking|exclusive|just in|update|alert|news|daily|watch|report|thread|info|announcement|introducing|presents?|announcing)\b[:\s-]*",
    re.IGNORECASE
)
_CHAIN_WORDS = {
    "base", "arbitrum", "ethereum", "solana", "optimism", "linea", "polygon", "avax",
    "avalanche", "bsc", "binance", "scroll", "zksync", "mantle", "berachain", "monad", "sui", "aptos"
}
# Short tokens that are nonetheless significant for dedup (version bumps are a prime
# duplicate class: "Aave V3 on Base" vs "Aave deploys V3 to Base"). Kept despite len<3.
_VERSION_RE = re.compile(r"^v\d+$")


def get_title_words(title: str) -> set[str]:
    """Normalize a title and extract unique significant words."""
    if not title:
        return set()
    # Strip HTML tags
    title = re.sub(r"<[^>]+>", "", title)
    title = _PREFIX_RE.sub("", title).strip()
    title = _EMOJI_RE.sub(" ", title.lower())
    words = {
        w for w in re.split(r"\s+", title)
        if w and w not in _DEDUP_STOP and (len(w) >= 3 or _VERSION_RE.match(w))
    }
    return words


def is_semantic_duplicate(words1: set[str], words2: set[str]) -> bool:
    """Assess if two word sets represent a semantic duplicate."""
    if not words1 or not words2:
        return False
    # Chain matching heuristic: if specific chains are mentioned in both and do not overlap, not duplicate
    chains1 = words1.intersection(_CHAIN_WORDS)
    chains2 = words2.intersection(_CHAIN_WORDS)
    if chains1 and chains2 and chains1 != chains2:
        return False

    intersection = words1.intersection(words2)
    union = words1.union(words2)
    similarity = len(intersection) / len(union) if union else 0.0
    shorter_len = min(len(words1), len(words2))
    
    if similarity >= 0.6:
        return True
    if len(intersection) >= 3 and len(intersection) / shorter_len >= 0.75:
        return True
    return False


def _norm_title(t: str) -> str:
    """Fallback legacy title normalizer."""
    t = _EMOJI_RE.sub(" ", t.lower())
    words = [w for w in re.split(r"\s+", t) if w and w not in _DEDUP_STOP]
    return " ".join(words[:6])


def _significant_words(text: str) -> list[str]:
    """Fallback corroboration terms for entity-less bullets: long, non-stopword tokens."""
    toks = [w for w in re.split(r"\W+", text.lower()) if len(w) >= 4 and w not in _DEDUP_STOP]
    # de-dup preserving order, cap to keep the regex small
    seen, out = set(), []
    for w in toks:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:6]


# ── Source Credibility Tiers ────────────────────────────────────────────────
FEED_CREDIBILITY = {
    # High Trust (Tier 1) - weight 1.2
    "@vitalikbuterin": 1.2,
    "@timbeiko": 1.2,
    "@ethereumfndn": 1.2,
    "@ethereum": 1.2,
    "@l2beat": 1.2,
    "@defillama": 1.2,
    "@tokenterminal": 1.2,
    "@dune": 1.2,
    "@nansen_ai": 1.2,
    "@arkham": 1.2,
    "@glassnode": 1.2,
    "@coingecko": 1.2,
    "@lookonchain": 1.2,
    "@peckshield": 1.2,
    "@slowmist_team": 1.2,
    "@messaricrypto": 1.2,
    "@bitmexresearch": 1.2,
    "@snapshotlabs": 1.2,
    "@gauntlet_xyz": 1.2,
    "@llamarisk": 1.2,
    "@stablelab": 1.2,
    "@seedgov": 1.2,
    "@coinbase": 1.2,
    "@binance": 1.2,
    "@krakenfx": 1.2,
    "theblock.co": 1.2,
    "decrypt.co": 1.2,
    "blockworks.co": 1.2,
    "coindesk.com": 1.2,
    "bankless.com": 1.2,
    "insights.glassnode.com": 1.2,
    
    # Low Trust / Clickbait (Tier 3) - weight 0.4
    "@watcherguru": 0.4,
    "@wublockchain": 0.4,
}


def get_source_key(link: str) -> str:
    """Extract domain or Twitter handle as the identifier for a source."""
    if not link:
        return ""
    parsed = urlparse(link)
    netloc = parsed.netloc.lower().removeprefix("www.")
    if netloc in ("x.com", "twitter.com", "nitter.net"):
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            return f"@{parts[0].lower()}"
    return netloc


def get_source_credibility(source_key: str) -> float:
    """Get credibility score for a source key (defaulting to Tier 2 = 1.0)."""
    return FEED_CREDIBILITY.get(source_key.lower(), 1.0)


# ── Reference data (loaded once per digest run) ─────────────────────────────
class _RefData:
    def __init__(self) -> None:
        # entity name/alias → mention_count, plus the searchable term list
        self.entity_terms: list[tuple[str, int]] = []   # (term_lower, mention_count)
        try:
            for name, aliases, mentions in db.get_entity_mention_map():
                terms = [name] + list(aliases or [])
                for term in terms:
                    if term and len(term) >= 3:
                        self.entity_terms.append((term, mentions or 0))
        except Exception:
            log.debug("scoring: could not load entity mention map", exc_info=True)
        # protocol name → tvl_usd
        self.protocol_tvls: list[tuple[str, float]] = []
        try:
            self.protocol_tvls = [
                (n, float(t)) for n, t in db.get_protocol_tvls() if n and t is not None
            ]
        except Exception:
            log.debug("scoring: could not load protocol TVLs", exc_info=True)
        # covered story word sets from last 7 days (novelty)
        self.covered_word_sets: list[set[str]] = []


def _build_covered_word_sets(before_date: date_t) -> list[set[str]]:
    """Normalized title word sets of the last 7 days of digests (excluding the date)."""
    word_sets: list[set[str]] = []
    try:
        rows = db.get_digest_contents_for_dedup(days=7, before_date=before_date)
    except Exception:
        log.debug("scoring: could not load dedup context", exc_info=True)
        return word_sets
    title_re = re.compile(r"<b>([\s\S]*?)</b>", re.I)
    tag_re = re.compile(r"<[^>]+>")
    for _d, content in rows:
        if not content:
            continue
        for m in title_re.finditer(content):
            title = tag_re.sub("", m.group(1)).strip()
            w = get_title_words(title)
            if w:
                word_sets.append(w)
    return word_sets


# ── Individual signals ──────────────────────────────────────────────────────
def _bullet_entities(text: str, ref: _RefData) -> list[tuple[str, int]]:
    """Word-boundary match the bullet text against known entity terms.
    Returns [(term, mention_count)] for matched entities."""
    found = []
    for term, mentions in ref.entity_terms:
        if re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE):
            found.append((term, mentions))
    return found


def _signal_corroboration(credibilities: list[float]) -> int:
    """Compute corroboration score based on the sum of credibility weights."""
    total_credibility = sum(credibilities)
    if total_credibility >= 3.0:
        return 25
    if total_credibility >= 2.0:
        return 20
    if total_credibility >= 1.0:
        return 12
    if total_credibility >= 0.4:
        return 5
    return 0


def _signal_amount(text: str) -> int:
    best = 0.0
    for num, unit in _AMOUNT_RE.findall(text):
        try:
            val = float(num.replace(",", ""))
        except ValueError:
            continue
        val *= _MULT.get((unit or "").lower(), 1.0)
        best = max(best, val)
    if best >= 1e9:
        return 20
    if best >= 5e8:
        return 16
    if best >= 1e8:
        return 12
    if best >= 1e7:
        return 7
    if best >= 1e6:
        return 3
    return 0


def _signal_velocity(timestamps: list[datetime]) -> int:
    """Max items inside a rolling window: 5+/3h→15, 3+/6h→10, 2+/12h→5."""
    if len(timestamps) < 2:
        return 0
    ts = sorted(t for t in timestamps if t is not None)

    def max_in_window(hours: float) -> int:
        span = hours * 3600
        best, j = 1, 0
        for i in range(len(ts)):
            while (ts[i] - ts[j]).total_seconds() > span:
                j += 1
            best = max(best, i - j + 1)
        return best

    if max_in_window(3) >= 5:
        return 15
    if max_in_window(6) >= 3:
        return 10
    if max_in_window(12) >= 2:
        return 5
    return 0


def _signal_entity_weight(entities: list[tuple[str, int]], ref: _RefData) -> int:
    best = 0
    for term, mentions in entities:
        # TVL component — protocol whose name word-boundary-contains the entity term
        tvl = 0.0
        pat = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        for pname, ptvl in ref.protocol_tvls:
            if pat.search(pname):
                tvl = max(tvl, ptvl)
        if tvl > 5e9:
            tvl_score = 20
        elif tvl > 1e9:
            tvl_score = 14
        elif tvl > 1e8:
            tvl_score = 8
        elif tvl > 1e7:
            tvl_score = 4
        else:
            tvl_score = 0
        # Mention component
        if mentions > 50:
            mention_score = 10
        elif mentions > 20:
            mention_score = 6
        elif mentions > 5:
            mention_score = 3
        else:
            mention_score = 0
        best = max(best, tvl_score, mention_score)
    return best


def _signal_criticality(text: str) -> int:
    for score in sorted(_KEYWORD_PATTERNS, reverse=True):
        if any(p.search(text) for p in _KEYWORD_PATTERNS[score]):
            return score
    return 0


def _signal_novelty(title: str, ref: _RefData) -> int:
    words = get_title_words(title)
    if not words:
        return 5
    for cw in ref.covered_word_sets:
        if is_semantic_duplicate(words, cw):
            return 0
    return 5


# ── Decay ────────────────────────────────────────────────────────────────────
def _apply_decay(score: int, first_seen_at: datetime, ref_time: datetime) -> tuple[int, float]:
    age_hours = max(0.0, (ref_time - first_seen_at).total_seconds() / 3600)
    decay = max(0.75, 1.0 - (age_hours / 48.0) * 0.25)
    return round(score * decay), round(decay, 3)


# ── Public entry point ───────────────────────────────────────────────────────
def compute_importance_scores(bullets: list[dict], digest_date: str) -> list[dict]:
    """Enrich each bullet with importance_score, source_count, score_breakdown.

    bullets: [{title, body}] (other keys passed through untouched).
    digest_date: 'YYYY-MM-DD'. Returns the same list; on total failure every
    bullet gets importance_score=source_count=score_breakdown=None.
    """
    out = [dict(b) for b in bullets]
    if not out:
        return out
    try:
        day = date_t.fromisoformat(digest_date)
    except (ValueError, TypeError):
        day = datetime.now(timezone.utc).date()

    # Reference time for decay: now() for a live run, else end-of-digest-day.
    now = datetime.now(timezone.utc)
    ref_time = now if day >= now.date() else datetime.combine(day, time(9, 0), tzinfo=timezone.utc)

    try:
        ref = _RefData()
        ref.covered_word_sets = _build_covered_word_sets(day)
    except Exception:
        log.warning("scoring: reference data load failed — scores set to None", exc_info=True)
        for b in out:
            b["importance_score"] = b["source_count"] = b["score_breakdown"] = None
        return out

    # ── Per-bullet Python signals ──
    for b in out:
        try:
            title = b.get("title", "") or ""
            body = b.get("body", "") or ""
            text = f"{title} {body}".strip()
            entities = _bullet_entities(text, ref)
            terms = [t for t, _ in entities] or _significant_words(text)

            feed = db.get_feed_items_matching_terms(terms, day, window_hours=24) if terms else []
            
            # Source key and credibility parsing
            source_keys = {get_source_key(r["link"]) for r in feed if r.get("link")}
            source_keys.discard("")
            credibilities = [get_source_credibility(sk) for sk in source_keys]
            
            # Domain tracking (for legacy schema compatibility)
            domains = {
                urlparse(r["link"]).netloc.lower().removeprefix("www.")
                for r in feed if r.get("link")
            }
            domains.discard("")

            timestamps = [r["ts"] for r in feed if r.get("ts")]
            # amount text includes source descriptions when available
            amount_text = text + " " + " ".join((r.get("content") or "")[:300] for r in feed[:10])

            s1 = _signal_corroboration(credibilities)
            s2 = _signal_amount(amount_text)
            s3 = _signal_velocity(timestamps)
            s4 = _signal_entity_weight(entities, ref)
            s5 = _signal_criticality(text)
            s6 = _signal_novelty(title, ref)
            p_total = min(100, s1 + s2 + s3 + s4 + s5 + s6)

            # Apply low credibility penalty if only Tier 3 clickbait sources reported it
            max_cred = max(credibilities) if credibilities else 1.0
            if max_cred <= 0.4:
                p_total = round(p_total * 0.5)

            b["_entities"] = entities
            b["_python_total"] = p_total
            b["_source_count"] = len(domains)
            b["_first_seen_at"] = min(timestamps) if timestamps else ref_time
            b["_breakdown_partial"] = {
                "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "s6": s6,
                "python_total": p_total,
            }
        except Exception:
            log.debug("scoring: signal computation failed for %r", b.get("title"), exc_info=True)
            b["_entities"] = []
            b["_python_total"] = 0
            b["_source_count"] = 0
            b["_first_seen_at"] = ref_time
            b["_breakdown_partial"] = {"s1": 0, "s2": 0, "s3": 0, "s4": 0,
                                       "s5": 0, "s6": 0, "python_total": 0}

    # ── Finalize: decay (LLM passes removed) ──
    for b in out:
        final, decay = _apply_decay(b["_python_total"], b["_first_seen_at"], ref_time)
        final = max(0, min(100, final))

        breakdown = dict(b["_breakdown_partial"])
        breakdown.update({
            "llm_adjustment": 0,
            "position_bonus": 0,
            "decay": decay,
        })
        b["importance_score"] = final
        b["source_count"] = b["_source_count"]
        b["score_breakdown"] = breakdown

        # strip internal scratch keys
        for k in ("_entities", "_python_total", "_source_count", "_first_seen_at",
                  "_breakdown_partial"):
            b.pop(k, None)

    return out

