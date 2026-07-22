"""Importance scoring for daily-digest bullets (0–100) + corroborating source count.

Fully deterministic — NO LLM. (The two LLM calibration/ranking passes were removed:
they added cost + free-tier rate-limit risk for little signal over the Python signals.)

Pipeline (per bullet):
  1. Python signals (deterministic, summed then capped at 100):
       s1 corroboration: SUM of source CREDIBILITY weights, not a raw count  (0–25)
          (log-spaced bands calibrated to the measured 0–90 credibility-sum
           distribution — a single premium Kaiko item (3.0) still tops the band;
           three unknown Tier-2 accounts cross-posting caps at 13, not max)
       s2 financial magnitude — scans the bullet's OWN text only             (0–20)
       s3 appearance velocity — earliest item per DISTINCT source            (0–15)
       s4 entity weight (TVL or 30d mention count)                           (0–20)
       s5 semantic criticality (keyword buckets)                             (0–15)
       s6 novelty vs last 7 days of digests (graded 0/2/5, Jaccard echoes)   (0– 5)
       s7 saturation PENALTY: protocol already dominates recent coverage     (0–-12)
          (bypassed for hacks/≥$500M events so real news is never buried)
  2. Credibility penalty: ×0.5 when ONLY Tier-3 (clickbait) sources reported it.
  3. Temporal decay (story age at digest time).

Corroboration/velocity read a 48h feed window ending at the digest date — a story
that broke the previous morning still earns s1/s3 (the old 24h cliff zeroed both
for ~12% of bullets); decay discounts the age instead of erasing the credit.

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

from . import db
from .entities import matchable_term

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
    # "phishing"/"stolen"/"theft"/"seized" added 2026-07-01: theft events phrased without
    # "hack"/"exploit" ("Polymarket phishing loss") were scoring s5=0.
    15: ["hack", "exploit", "drain", "blacklist", "freeze", "rug", "breach", "attack",
         "phishing", "stolen", "theft", "seized"],
    # Funding & M&A rank alongside governance — capital flows are high-signal events the
    # feed should surface. ("series a"/"series b" are kept as phrases to avoid matching a
    # bare "series".) Paired with the digest-prompt FUNDING & M&A include rule.
    # "shuts"/"buys"/"ipo" etc. added 2026-07-01: half the corpus scored s5=0 while being
    # plainly typed events ("Sophon shuts L2", "SBI buys Bitbank") the list just missed.
    11: ["vulnerability", "emergency", "pause", "governance", "vote", "proposal", "shutdown",
         "shuts", "shut down", "winds down", "wind down",
         "raise", "raises", "raised", "funding", "fundraise", "seed round",
         "series a", "series b", "acquire", "acquires", "acquired", "acquisition",
         "merger", "buyout", "buys", "buyback", "ipo"],
    7:  ["mainnet", "upgrade", "v2", "v3", "launch", "audit", "migration", "deploy",
         "debut", "listing", "lists", "testnet"],
    3:  ["partnership", "integration", "update", "support", "adds", "enables"],
}
_KEYWORD_PATTERNS = {
    score: [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in words]
    for score, words in _KEYWORDS.items()
}

# Coarse category label for an item, derived from the SAME criticality keyword buckets above
# (single-sourced — never fork the vocab). Used by the breaking-news alert path (app/alerts.py):
# 'security' is the hard-trigger category; the rest just tag the alert line. Ordered by priority.
_CATEGORY_KEYWORDS = [
    ("security", _KEYWORDS[15]),
    ("funding", ["raise", "raises", "raised", "funding", "fundraise", "seed round",
                 "series a", "series b", "acquire", "acquires", "acquired", "acquisition",
                 "merger", "buyout", "buys", "buyback", "ipo"]),
    ("governance", ["governance", "vote", "proposal", "emergency", "pause", "shutdown",
                    "shuts", "shut down", "winds down", "wind down"]),
    ("launch", _KEYWORDS[7]),
    ("partnership", _KEYWORDS[3]),
]
_CATEGORY_PATTERNS = [
    (label, [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in words])
    for label, words in _CATEGORY_KEYWORDS
]


def _matched_category(text: str) -> str:
    """Coarse event category from the criticality keyword buckets ('' if none match)."""
    for label, pats in _CATEGORY_PATTERNS:
        if any(p.search(text) for p in pats):
            return label
    return ""


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


def _is_near_duplicate(words1: set[str], words2: set[str]) -> bool:
    """Looser duplicate test used ONLY by the novelty signal (s6).

    Bullets reaching scoring already passed the digest's pre-filter, which drops
    candidates via the strict ``is_semantic_duplicate`` above (app/digest.py) —
    so by construction that test can never fire here and s6 was a constant +5
    for 60 straight days. This variant (Jaccard ≥0.45, or ≥2 shared words
    covering ≥⅔ of the shorter set) catches the near-repeats that slip the
    gate. Do NOT use it for hard dedup decisions — it over-matches on purpose.
    """
    if not words1 or not words2:
        return False
    chains1 = words1 & _CHAIN_WORDS
    chains2 = words2 & _CHAIN_WORDS
    if chains1 and chains2 and chains1 != chains2:
        return False
    intersection = words1 & words2
    union = words1 | words2
    if union and len(intersection) / len(union) >= 0.45:
        return True
    shorter_len = min(len(words1), len(words2))
    return len(intersection) >= 2 and len(intersection) / shorter_len >= 2 / 3


def _is_soft_echo(words1: set[str], words2: set[str]) -> bool:
    """Weakest overlap test — a partial thematic repeat that is NOT a near-duplicate.

    Used ONLY by the graded novelty signal (s6) to distinguish a fully-fresh story
    from one that softly echoes recent coverage (same protocol, adjacent angle).
    Called after ``_is_near_duplicate`` has already returned False, so it fires on the
    gap below that gate: Jaccard in [0.30, 0.45), or ≥2 shared significant words that
    don't cover ⅔ of the shorter set. Chain-disjoint titles are never echoes.
    """
    if not words1 or not words2:
        return False
    chains1 = words1 & _CHAIN_WORDS
    chains2 = words2 & _CHAIN_WORDS
    if chains1 and chains2 and chains1 != chains2:
        return False
    intersection = words1 & words2
    union = words1 | words2
    if union and len(intersection) / len(union) >= 0.30:
        return True
    return len(intersection) >= 2


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
    # The Glassnode blog moved hosts (2026-07-07, see feeds.py) — keep BOTH keys so historical
    # items keep corroborating and new ones are weighted Tier-1.
    "research.glassnode.com": 1.2,
    # The Tier-1 news outlets ALSO publish via Twitter — get_source_key maps those items
    # to @handles, not domains, so both keys are needed or ~80% of their volume (e.g.
    # @theblockco, the single biggest source in the corpus) is silently weighted Tier-2.
    "@theblockco": 1.2,
    "@coindesk": 1.2,
    "@decryptmedia": 1.2,
    "@blockworks": 1.2,
    # Kaiko: premium institutional research (exclusive data/analysis, no RSS — scraped directly).
    # Weight 3.0 means a single Kaiko article alone reaches the ≥3.0 corroboration threshold → s1=25 (max).
    # (No "www." variant: get_source_key strips the prefix before lookup.)
    "kaiko.com": 3.0,
    "@kaikodata": 3.0,  # Kaiko's Twitter — same institutional source

    # Low Trust / Clickbait (Tier 3) - weight 0.4
    "@watcherguru": 0.4,
    "@wublockchain": 0.4,
    # AI "alpha" bots: high-velocity, often overstate announcements as facts (e.g. framed
    # Circle's testnet-only Arc as a live chain with "forced" migrations). Discount their weight.
    "@aixbt_agent": 0.4,
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
            for name, aliases, type_, mentions in db.get_entity_mention_map():
                terms = [name] + list(aliases or [])
                for term in terms:
                    # matchable_term (entities.py) is the ONE shared false-positive
                    # gate. Without it, junk single-word aliases ("Onchain", "Public",
                    # "Notional") OR-match half the corpus and hand bullets a maxed
                    # corroboration/velocity signal built on noise.
                    if term and len(term) >= 3 and matchable_term(term, type_, mentions or 0):
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
        # entity term (lower) → number of distinct recent digest days it was covered
        # (Signal 7 saturation: dampens protocols that already dominate recent coverage).
        self.recent_entity_coverage: dict[str, int] = {}


# ── Signal 7: recent-coverage saturation ─────────────────────────────────────
# Base-layer / generic terms that appear in nearly every DeFi bullet — they would
# trip the saturation penalty constantly, so they NEVER trigger it (the penalty is
# meant to thin out specific over-covered protocols, not Ethereum/Bitcoin/USDC).
_SATURATION_EXCLUDE = frozenset({
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "defi",
    "stablecoin", "stablecoins", "usdc", "usdt", "tether", "dai", "weth",
}) | _CHAIN_WORDS


def _build_recent_entity_coverage(
    entity_terms: list[tuple[str, int]], before_date: date_t
) -> dict[str, int]:
    """For each entity term, count DISTINCT days in the last 7 digests it was covered.

    This is the signal behind the saturation penalty: a protocol mentioned in a bullet
    on ≥3 of the last 7 days is "saturating" the feed, so a routine new bullet about it
    is worth less (unless it carries genuinely new weight — see ``_signal_saturation``).
    """
    coverage: dict[str, set] = {}
    try:
        rows = db.get_digest_contents_for_dedup(days=7, before_date=before_date)
    except Exception:
        log.debug("scoring: could not load coverage context", exc_info=True)
        return {}
    tag_re = re.compile(r"<[^>]+>")
    pats = [
        (term.lower(), re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE))
        for term, _ in entity_terms
        if term.lower() not in _SATURATION_EXCLUDE
    ]
    for d, content in rows:
        if not content:
            continue
        text = tag_re.sub(" ", content)
        for term_l, pat in pats:
            if pat.search(text):
                coverage.setdefault(term_l, set()).add(d)
    return {term_l: len(days) for term_l, days in coverage.items()}


def _signal_saturation(entities: list[tuple[str, int]], ref: _RefData) -> int:
    """Penalty (0–12) for bullets about a protocol that already dominates recent coverage.

    Returns the penalty to SUBTRACT from the bullet's positive signals. Anchored to the
    most-saturated entity in the bullet: covered on ≥5 of the last 7 days → 12, ≥3 → 7.
    """
    best = 0
    for term, _ in entities:
        best = max(best, ref.recent_entity_coverage.get(term.lower(), 0))
    if best >= 5:
        return 12
    if best >= 3:
        return 7
    return 0


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
    """Corroboration score (0–25) from the SUM of source credibility weights.

    Log-spaced bands (2026-07-11 recalibration, T1): the old bands topped out at a
    total of ≥3–4, but a 30-day replay showed digest bullets are heavily corroborated
    — the credibility sum ranges 0…90 (median ~8) — so 83% of bullets hit the 25 band
    and s1 stopped separating stories (every daily top-5 bullet scored 25). The bands
    below roughly DOUBLE each step, matching that measured distribution so the signal
    spreads across the full 0–25 range and does ranking work again.

    Two documented properties are preserved:
      - a single Kaiko article (weight 3.0) still reaches the top band alone — the
        premium-tier override (``max_credibility >= 3.0``);
      - three unknown Tier-2 accounts cross-posting (total 3.0, no trusted/premium
        source) caps at 13, nowhere near max — the cheapest republication attack.
    """
    total = sum(credibilities)
    # Premium single-source override: Kaiko (3.0) alone tops the band. No non-premium
    # combination of Tier-1/2 sources can reach 3.0 in a single source, so this only
    # fires for the premium tier.
    if max(credibilities, default=0.0) >= 3.0:
        return 25
    if total >= 24:
        return 25
    if total >= 12:
        return 21
    if total >= 6:
        return 17
    if total >= 3:
        return 13
    if total >= 1.5:
        return 9
    if total >= 0.8:
        return 5
    if total >= 0.4:
        return 2
    return 0


def _max_amount_usd(text: str) -> float:
    """Largest USD magnitude mentioned in `text` (0.0 if none). The raw dollar value behind
    _signal_amount's band — the alert hard-trigger needs the number, not the 0–20 score."""
    best = 0.0
    for num, unit in _AMOUNT_RE.findall(text):
        try:
            val = float(num.replace(",", ""))
        except ValueError:
            continue
        best = max(best, val * _MULT.get((unit or "").lower(), 1.0))
    return best


def _signal_amount(text: str) -> int:
    best = _max_amount_usd(text)
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
    """Graded novelty vs the last 7 days of digest titles (0/2/5).

    5 = genuinely fresh · 2 = soft echo of recent coverage (same subject, new angle)
    · 0 = near-duplicate. The binary 5/0 version was effectively a constant +5 (it fired
    0 → 195/213 bullets scored 5 over the replay window) because the strict near-dup gate
    almost never matched a *title* against prior titles. The middle tier gives s6 real
    variance so a follow-up story ("Aave V4 hits $250M" after "Aave V4 launches") ranks
    below a brand-new one.
    """
    words = get_title_words(title)
    if not words:
        return 5
    soft = False
    for cw in ref.covered_word_sets:
        if _is_near_duplicate(words, cw):
            return 0
        if not soft and _is_soft_echo(words, cw):
            soft = True
    return 2 if soft else 5


# ── Decay ────────────────────────────────────────────────────────────────────
def _apply_decay(score: int, first_seen_at: datetime, ref_time: datetime) -> tuple[int, float]:
    age_hours = max(0.0, (ref_time - first_seen_at).total_seconds() / 3600)
    decay = max(0.75, 1.0 - (age_hours / 48.0) * 0.25)
    return round(score * decay), round(decay, 3)


# ── Shared reference data + per-item signal core ─────────────────────────────
def build_ref_data(day: date_t) -> "_RefData":
    """Load the once-per-run reference data (entity map, protocol TVLs, covered-word sets,
    recent-coverage saturation) anchored at `day`. Shared by compute_importance_scores and
    the alert path (app/alerts.py) so both score against the same corpus state."""
    ref = _RefData()
    ref.covered_word_sets = _build_covered_word_sets(day)
    ref.recent_entity_coverage = _build_recent_entity_coverage(ref.entity_terms, day)
    return ref


def _score_item_signals(title: str, body: str, ref: "_RefData", day: date_t,
                        ref_time: datetime) -> dict:
    """Compute the deterministic Python signals (s1..s7 → python_total, pre-decay) for one
    item — a digest bullet OR a raw feed item. This is the SINGLE implementation of the signal
    math; compute_importance_scores and score_item both call it (no re-rolled scoring).

    Returns python_total/source_count/first_seen_at/breakdown_partial exactly as the old inline
    loop produced, PLUS max_credibility/amount_usd/category that the alert path needs (the
    digest path simply ignores those extra keys)."""
    text = f"{title} {body}".strip()
    entities = _bullet_entities(text, ref)
    terms = [t for t, _ in entities] or _significant_words(text)

    # 48h window (was 24h): the old cliff zeroed s1/s3 for stories that broke the previous
    # morning (~12% of bullets); decay discounts age instead.
    feed = db.get_feed_items_matching_terms(terms, day, window_hours=48) if terms else []
    if not entities and len(terms) >= 2:
        # Entity-less items fall back to generic significant words, and the DB match is an OR —
        # "volume" or "governance" alone matches half the corpus and would max s1/s3 on pure
        # noise. Require ≥2 distinct fallback terms so only genuinely related coverage counts.
        pats = [re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE) for t in terms]
        feed = [r for r in feed
                if sum(1 for p in pats if p.search(r.get("content") or "")) >= 2]

    # Thin_content items don't corroborate (a 50-char teaser is not evidence a story is real),
    # but still count for velocity (publication timing is valid evidence either way).
    substantive_feed = [r for r in feed if r.get("quality_flag", "ok") != "thin_content"]
    source_keys = {get_source_key(r["link"]) for r in substantive_feed if r.get("link")}
    source_keys.discard("")
    credibilities = [get_source_credibility(sk) for sk in source_keys]

    # Velocity counts distinct-SOURCE pickup — earliest item per source key (thin or not).
    first_ts_by_source: dict[str, datetime] = {}
    for r in feed:
        ts = r.get("ts")
        sk = get_source_key(r.get("link") or "")
        if ts and sk and (sk not in first_ts_by_source or ts < first_ts_by_source[sk]):
            first_ts_by_source[sk] = ts
    timestamps = [r["ts"] for r in feed if r.get("ts")]

    s1 = _signal_corroboration(credibilities)
    # Financial magnitude reads the item's OWN text only (scanning corroborators imported
    # ambient figures into unrelated items).
    s2 = _signal_amount(text)
    s3 = _signal_velocity(list(first_ts_by_source.values()))
    s4 = _signal_entity_weight(entities, ref)
    s5 = _signal_criticality(text)
    s6 = _signal_novelty(title, ref)
    p_total = min(100, s1 + s2 + s3 + s4 + s5 + s6)

    # Signal 7 (penalty): recent-coverage saturation — bypassed for a hack/exploit (s5≥15) or
    # a ≥$500M event (s2≥16) so real news is never buried.
    s7 = _signal_saturation(entities, ref)
    if s5 >= 15 or s2 >= 16:
        s7 = 0
    p_total = max(0, p_total - s7)

    # Low-credibility penalty: only Tier-3 clickbait reported it.
    max_cred = max(credibilities) if credibilities else 1.0
    if max_cred <= 0.4:
        p_total = round(p_total * 0.5)

    return {
        "entities": entities,
        "python_total": p_total,
        # Distinct substantive SOURCES (same base as s1).
        "source_count": len(source_keys),
        "first_seen_at": min(timestamps) if timestamps else ref_time,
        "breakdown_partial": {
            "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5, "s6": s6,
            "s7_saturation": -s7,
            "python_total": p_total,
        },
        "max_credibility": max_cred,
        "amount_usd": _max_amount_usd(text),
        "category": _matched_category(text),
    }


def prescreen_category_amount(text: str) -> tuple[str, float]:
    """Cheap, NO-DB signals for an item's text: (category, largest_usd_amount). The alert path
    (app/alerts.py) uses this to skip the expensive corroboration scoring for items that could
    never clear a high score floor — an item with no event category AND no $ amount tops out at
    s1+s3+s4+s6 = 25+15+20+5 = 65 (pre-decay), below the default ALERT_MIN_SCORE of 70."""
    return _matched_category(text), _max_amount_usd(text)


def score_item(item: dict, ref: "_RefData", day: date_t,
               ref_time: "datetime | None" = None) -> dict:
    """Score a single feed item for the breaking-news alert path (app/alerts.py).

    `item` = {title, content, ts} (a feed_items row). Reuses the SAME signal math as the daily
    digest via _score_item_signals, then applies temporal decay off the item's own timestamp.
    `ref` is built once per scan with build_ref_data(day). Returns
    {score (0-100, decayed), source_count, category, amount_usd, max_credibility, breakdown}.
    """
    if ref_time is None:
        ref_time = datetime.now(timezone.utc)
    sig = _score_item_signals(item.get("title") or "", item.get("content") or "",
                              ref, day, ref_time)
    first_seen = item.get("ts") or sig["first_seen_at"]
    final, decay = _apply_decay(sig["python_total"], first_seen, ref_time)
    final = max(0, min(100, final))
    breakdown = dict(sig["breakdown_partial"])
    breakdown["decay"] = decay
    return {
        "score": final,
        "source_count": sig["source_count"],
        "category": sig["category"],
        "amount_usd": sig["amount_usd"],
        "max_credibility": sig["max_credibility"],
        "breakdown": breakdown,
    }


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
        ref = build_ref_data(day)
    except Exception:
        log.warning("scoring: reference data load failed — scores set to None", exc_info=True)
        for b in out:
            b["importance_score"] = b["source_count"] = b["score_breakdown"] = None
        return out

    # ── Per-bullet Python signals (shared core: _score_item_signals) ──
    for b in out:
        try:
            sig = _score_item_signals(b.get("title", "") or "", b.get("body", "") or "",
                                      ref, day, ref_time)
            b["_entities"] = sig["entities"]
            b["_python_total"] = sig["python_total"]
            b["_source_count"] = sig["source_count"]
            b["_first_seen_at"] = sig["first_seen_at"]
            b["_breakdown_partial"] = sig["breakdown_partial"]
        except Exception:
            log.debug("scoring: signal computation failed for %r", b.get("title"), exc_info=True)
            b["_entities"] = []
            b["_python_total"] = 0
            b["_source_count"] = 0
            b["_first_seen_at"] = ref_time
            b["_breakdown_partial"] = {"s1": 0, "s2": 0, "s3": 0, "s4": 0,
                                       "s5": 0, "s6": 0, "s7_saturation": 0,
                                       "python_total": 0}

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

