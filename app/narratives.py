"""Narrative intelligence layer — cluster cross-source signals into persistent
narratives carrying a momentum state (forming / heating / steady / cooling / dormant).

Pipeline (full rebuild, run post-digest + on a cron):
  1. Gather signals from three sources over a lookback window:
       news        — digest_bullet_analysis (already importance-scored 0–100)
       podcast     — podcast_episodes.analysis (notable_claims / predictions)
       governance  — governance_proposals
  2. Resolve entities per signal (word-boundary match vs entity_memory).
  3. Embed each signal (Ollama, 768-dim) and greedily cluster by
       entity overlap (primary) + embedding cosine (support).
  4. Compute momentum per cluster (mass = importance/100, windows anchored to a
       reference time — mirrors app/scoring.py).
  5. Synthesize a label + thesis + watch-next + contrarian per cluster (LLM,
       reusing unchanged clusters to bound cost).
  6. Persist via db.replace_narratives (wipe + insert — safe full rebuild).

Best-effort throughout: any failure is logged, never raised — narrative rebuild
must never break the digest that triggers it.

CLI:
  python -m app.narratives                 # rebuild now (default 30-day window)
  python -m app.narratives --days 21       # wider window
  python -m app.narratives --no-persist    # print clusters, don't write
  python -m app.narratives --no-llm        # heuristic labels only (no LLM calls)
"""
from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_t, datetime, time, timedelta, timezone

from . import config, db, embeddings, entities, llm, prompts, util

log = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────
# A "narrative" is a theme that PERSISTS over weeks, not a 48-hour news burst. The
# gates below enforce that: a wide lookback, ≥3 corroborating signals, and coverage
# spread across ≥2 distinct days (a single-day cluster is one event, not a narrative).
WINDOW_DAYS = 45           # signal lookback — wide enough to surface multi-week narratives
MIN_SIGNALS = 3            # a real narrative needs ≥3 corroborating signals (not a 1-off)
MIN_SPAN_DAYS = 2          # …spread across ≥2 distinct days (drops single-day news bursts)
BASELINE_HOURS = 504       # momentum baseline window (21d): ρ = recent 48h vs 3-week trend
COSINE_STRONG = 0.88       # embedding-only merge threshold — crypto text is dense, needs higher bar
COSINE_SUPPORT = 0.75      # embedding threshold when ≥1 shared entity
MIN_SIGNAL_RELEVANCE = 0.62  # post-cluster prune: drop signals below this cosine to recomputed centroid
MAX_LLM_SYNTHESES = 20     # cap LLM synthesis calls per rebuild
# Momentum thresholds, recalibrated 2026-07-07 against the MEASURED distribution. The +1/+1
# Laplace smoothing in ρ=(R+1)/(B+1) squashes the ratio into ~[0.77, 1.18] at real signal
# masses (R and B are mostly <0.5 — ~7 scored bullets/day spread over 10+ clusters), so the
# old gates (heating ρ≥1.5 ∧ R≥1.2, cooling ρ≤0.7) were structurally unreachable: the board
# sat 10 steady / 8 dormant with ZERO heating/forming/cooling for weeks. New semantics:
#   heating — meaningfully above its own baseline (ρ≥1.15) with real recent mass (R≥0.5,
#             ≈ one strong signal in 48h). The ratio gate + the source-diversity cap below
#             still block single-account manufactured momentum.
#   cooling — a cluster that HAD a real baseline (B≥0.15) and went silent in the last 48h
#             (R=0), or the legacy deep-ratio drop (ρ≤0.7, only mega-clusters can reach it).
# Replay states with `python -m app.narratives --no-persist` before touching these again.
R_MIN = 0.5
RHO_HEATING = 1.15
COOLING_BASELINE_MIN = 0.15
DEFAULT_MASS = {"news": 0.5, "podcast": 0.55, "governance": 0.6, "market": 0.5}

# DAO governance proposals make poor narrative drivers: they're numerous, bursty
# (many proposals from one space in a short window spike velocity) and mostly
# obscure, so they flood the board with low-value governance-only clusters.
# Narratives are built from news + podcasts only. Flip to re-include them.
INCLUDE_GOVERNANCE = False

# Ubiquitous entities that must not, on their own, glue unrelated stories into one
# mega-narrative. They still count for display + embedding, just not for the
# entity-overlap merge test.
# aave appears in nearly every DeFi bullet (liquidations, rates, governance) and acts
# as a false super-connector, pulling unrelated clusters together and dominating labels.
BROAD_ENTITIES = frozenset({
    # L1s / base chains — mentioned in almost every DeFi story
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "defi",
    "stablecoin", "stablecoins",
    # Dominant stablecoins — appear as context in almost every DeFi story;
    # without this gate they become super-connectors that absorb every yield/lending
    # story into one mega-cluster regardless of actual topic.
    "usdc", "usdt", "tether", "circle",
    # DeFi super-connectors (appear across most protocol stories). These are popular
    # lending/yield primitives that show up as context in nearly every DeFi yield or
    # rate story; without this gate they false-glue unrelated clusters into one
    # mega-narrative and dominate the board (e.g. "USDC Yield Primacy On Morpho").
    "aave", "morpho", "pendle",
    # Off-chain entities that leak into coverage but aren't DeFi primitives
    "elon-musk", "stripe",
})

# Versioned protocol entities: legitimate cluster anchors (they carry version signal)
# but overrepresented enough that the board should show at most one narrative per entity.
# When the cap fires, smaller clusters are MERGED into the largest (signals preserved).
SEMI_BROAD = frozenset({
    "aave-v4", "aave-v3", "aave-v2",
    "uniswap-v4", "uniswap-v3", "uniswap-v2",
    "compound-v3", "compound-v2",
})
# Dominant unversioned protocols that recur across many clusters. Unlike SEMI_BROAD
# these are capped only when the protocol is the cluster's PROTAGONIST (appears in
# ≥ DOMINANT_PROTAGONIST_FRAC of the cluster's signals), so genuinely distinct stories
# where the protocol is merely incidental are NOT force-merged.
DOMINANT_PROTOCOLS = frozenset({"aave", "morpho", "pendle"})
DOMINANT_PROTAGONIST_FRAC = 0.6
MAX_CLUSTERS_PER_ENTITY = 1   # board diversity: at most 1 narrative per dominant entity

# Person-name label guard — catches LLM outputs like "Justin Sun" or "Vitalik Buterin"
# that are person names rather than market narratives. Pattern: 2-3 capitalised words with
# no narrative keyword present. When triggered, _synthesize() discards the label so the
# caller falls back to the heuristic label (entity names).
_PERSON_LABEL_RE = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+){1,2}$")
_NARRATIVE_KW_RE = re.compile(
    r"\b(surge|rotation|migrat\w*|expansion|compression|adoption|dominance|capital|"
    r"yield|arbitrage|flow|bridge|volume|tvl|defi|stablecoin|lending|staking|restaking|"
    r"protocol|network|chain|layer|rollup|dex|amm|swap|perp|vault|governance|liquidity|"
    r"incentive|reward|fee|rate|apy|apr|token|asset|market|trading|activity|"
    r"competition|trend|momentum|supply|demand|pressure|deployment|launch|upgrade|"
    r"integration|partnership|ecosystem|outflow|inflow|repricing|rally|sell-?off)\b",
    re.I,
)


def _looks_like_person(label: str) -> bool:
    """True when a label is purely a person's name with no market-narrative keyword."""
    return bool(_PERSON_LABEL_RE.match(label)) and not _NARRATIVE_KW_RE.search(label)


# Meta-signal exclusion: titles matching these patterns are multi-event summaries, not
# single incidents. They mention many entities and distort cluster centroids / evidence lists.
_META_SIGNAL_RE = re.compile(
    r"\b(roundup|recap|digest|weekly\s+update|market\s+update|weekly\s+summary|"
    r"governance\s+roundup|governance\s+update|news\s+roundup|newsletter)\b",
    re.I,
)

# ── Severity (mirrors web BulletItem.classifySeverity) ──────────────────────
_SEV_RED = re.compile(
    r"\b(hack(?:ed|s)?|exploit(?:ed|s)?|breach(?:ed|es)?|attack(?:ed|s)?|"
    r"vulnerabilit(?:y|ies)|drain(?:ed|s)?|stolen|steal|rug(?:s|ged|pull)?)\b", re.I)
_SEV_GOLD = re.compile(r"\b(governance|proposals?|vot(?:e|es|ing)|dao|upgrade[ds]?|v[34])\b", re.I)
_SEV_GREEN = re.compile(
    r"\b(launch(?:ed|es)?|deploy(?:ed|s|ment)?|yield|apy|integrat(?:ion|ions|ed|e)|partnerships?)\b", re.I)


def _severity(text: str) -> str:
    if _SEV_RED.search(text):
        return "red"
    if _SEV_GOLD.search(text):
        return "gold"
    if _SEV_GREEN.search(text):
        return "green"
    return "neutral"


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_NITTER_RE = re.compile(r"nitter\.\w+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-")


def _source_domain(url: str) -> str:
    """Extract a normalised domain from a signal URL for source-diversity counting.

    nitter.net and x.com are treated as the same domain (both are Twitter).
    """
    if not url:
        return ""
    try:
        from urllib.parse import urlparse as _up
        netloc = _up(url).netloc.lower().removeprefix("www.")
        if _NITTER_RE.match(netloc) or netloc in ("x.com", "twitter.com"):
            return "x.com"
        return netloc
    except Exception:
        return ""


# ── Research sector classification ────────────────────────────────────────────
# Deterministic (NO LLM) primary-sector tag for the research index — mirrors the
# ethos of app/scoring.py. Scored, not first-match: each sector accrues weight from
# label hits (×3, the strongest signal), entity-slug hits (×2), and thesis hits (×1),
# and the highest score wins (ties broken toward the earlier, more-specific rule).
# This means "Monad Stablecoin Yield Stack" lands on yield/stablecoins even though its
# thesis happens to mention "RWA collateral" in passing. Terms are word-boundary
# regexes so "arc" doesn't match "search" and "base" doesn't match "database".
_SECTOR_RULES: list[tuple[str, list[str]]] = [
    ("RWA & Tokenization", [r"rwa", r"real[- ]world asset", r"tokeniz\w*", r"securitiz\w*",
                            r"t-?bill", r"treasury bill", r"money market fund", r"ondo",
                            r"private credit", r"vbill", r"buidl"]),
    ("Stablecoins", [r"stablecoin\w*", r"usdc", r"usdt", r"usde", r"ausd", r"pyusd", r"frxusd",
                     r"gho", r"\bdai\b", r"tether", r"circle", r"ethena", r"\bpeg\b", r"depeg\w*"]),
    ("Staking & Restaking", [r"liquid stak\w*", r"liquid restak\w*", r"restak\w*", r"\blst\b",
                             r"\blrt\b", r"staking pool", r"\bsteth\b", r"\brseth\b"]),
    ("Lending & Yield", [r"lend\w*", r"borrow\w*", r"yield\w*", r"\bapy\b", r"\bapr\b", r"vault\w*",
                         r"collateral\w*", r"money market", r"morpho", r"aave", r"euler", r"pendle",
                         r"\bspark\b", r"fluid", r"fixed[- ]yield"]),
    ("Derivatives", [r"perp\w*", r"perpetual\w*", r"\boption\w*", r"futures", r"synthetic\w*",
                     r"basis trad\w*", r"funding rate", r"interest rate derivativ\w*"]),
    ("Cross-Chain & Interop", [r"bridg\w*", r"cross[- ]chain", r"interop\w*", r"omnichain", r"ccip",
                               r"layerzero", r"wormhole", r"chainlink", r"messaging layer"]),
    ("DEX & Trading", [r"\bdex\b", r"\bamm\b", r"swap\w*", r"uniswap", r"\bcurve\b", r"balancer",
                       r"order ?book", r"trading volume", r"market maker", r"liquidity pool"]),
    ("Layer 2 & Scaling", [r"layer[- ]?2", r"\bl2\b", r"rollup\w*", r"optimism", r"arbitrum",
                           r"\bbase\b", r"zk-?rollup", r"sequencer", r"scaling"]),
    # NB: app-chains whose DeFiLlama category better describes them (e.g. hyperliquid →
    # Derivatives) are intentionally NOT keyword-listed here — the category vote classifies them.
    ("Layer 1 & Infrastructure", [r"layer[- ]?1", r"\bl1\b", r"mainnet", r"validator\w*", r"consensus",
                                  r"monad", r"\barc\b", r"\bsei\b", r"\bsui\b", r"aptos", r"solana",
                                  r"avalanche", r"berachain"]),
    ("Market Structure", [r"\betf\b", r"custod\w*", r"\bsec\b", r"\bmica\b", r"regulat\w*", r"compliance",
                          r"listing\w*", r"\bcftc\b", r"prime broker\w*"]),
    ("Governance", [r"governance", r"proposal\w*", r"\bdao\b", r"tokenholder\w*", r"\bvote\w*"]),
    ("AI & DePIN", [r"\bdepin\b", r"ai agent\w*", r"autonomous agent\w*", r"inference",
                    r"\bgpu\b", r"compute network"]),
    ("NFTs & Gaming", [r"\bnft\w*", r"gaming", r"metaverse"]),
]
# Each term is matched on a word boundary. Prepending \b to an already-anchored term
# (e.g. r"\bsec\b") yields \b\b…, which collapses to a single boundary — harmless.
_SECTOR_COMPILED = [(s, [re.compile(r"\b" + t, re.I) for t in terms]) for s, terms in _SECTOR_RULES]
_SECTOR_DEFAULT = "DeFi"

# DeFiLlama protocol category (lower-cased) → research sector. These are ADDITIONAL
# GROUNDED votes inside _sector, never an override: live data shows category coverage is
# sparse and frequently peripheral (e.g. a CCIP-bridge cluster's only categorised entity
# is a "Liquid Restaking" token; an RWA cluster's is "Privacy"). The label/text keyword
# signal must still dominate, so a stray category can't hijack the sector. Unmapped
# categories cast no vote.
_CATEGORY_TO_SECTOR = {
    "dexs": "DEX & Trading", "dexes": "DEX & Trading", "dex aggregator": "DEX & Trading",
    "liquidity manager": "DEX & Trading", "indexes": "DEX & Trading",
    "derivatives": "Derivatives", "options": "Derivatives", "synthetics": "Derivatives",
    "basis trading": "Derivatives", "interest rate derivatives": "Derivatives",
    "prediction market": "Derivatives",
    "lending": "Lending & Yield", "cdp": "Lending & Yield", "cdp manager": "Lending & Yield",
    "rwa lending": "Lending & Yield", "uncollateralized lending": "Lending & Yield",
    "risk curators": "Lending & Yield", "onchain capital allocator": "Lending & Yield",
    "yield": "Lending & Yield", "yield aggregator": "Lending & Yield", "farm": "Lending & Yield",
    "leveraged farming": "Lending & Yield", "yield lottery": "Lending & Yield",
    "liquid staking": "Staking & Restaking", "liquid restaking": "Staking & Restaking",
    "restaking": "Staking & Restaking", "staking pool": "Staking & Restaking",
    "restaked btc": "Staking & Restaking",
    "rwa": "RWA & Tokenization",
    "bridge": "Cross-Chain & Interop", "canonical bridge": "Cross-Chain & Interop",
    "cross chain bridge": "Cross-Chain & Interop", "cross chain": "Cross-Chain & Interop",
    "stablecoins": "Stablecoins", "algo-stables": "Stablecoins",
    "dual-token stablecoin": "Stablecoins", "reserve currency": "Stablecoins",
    "chain": "Layer 1 & Infrastructure",
    "ai agents": "AI & DePIN",
    "governance incentives": "Governance",
    "payments": "Market Structure", "crypto card issuer": "Market Structure",
}


def _sector(label: str | None, thesis: str | None, entity_slugs: list[str] | None,
            cat_by_slug: dict[str, str] | None = None) -> str:
    """Primary research sector for a narrative (deterministic, scored). Keyword hits on
    the label (×3), entity-slug (×2) and thesis (×1) are the dominant signal; the
    DeFiLlama categories of the cluster's entities add grounded votes (protagonist ×3,
    others ×2) that break ties + sharpen clean DeFi-protocol clusters, but can't override
    a strong keyword signal."""
    hay_label = (label or "").lower()
    hay_text = (thesis or "").lower()
    slugs = entity_slugs or []
    hay_slugs = " ".join(slugs).lower()
    scores: dict[str, int] = {}
    for sector, pats in _SECTOR_COMPILED:
        sc = 0
        for p in pats:
            if p.search(hay_label):
                sc += 3
            if p.search(hay_slugs):
                sc += 2
            if p.search(hay_text):
                sc += 1
        if sc:
            scores[sector] = scores.get(sector, 0) + sc
    # DeFiLlama category votes. entity_slugs is frequency-ranked (see _top_entities), so
    # the first slug is the protagonist and earns a slightly heavier vote.
    if cat_by_slug:
        for idx, slug in enumerate(slugs):
            sec = _CATEGORY_TO_SECTOR.get((cat_by_slug.get(slug) or "").lower())
            if sec:
                scores[sec] = scores.get(sec, 0) + (3 if idx == 0 else 2)
    if not scores:
        return _SECTOR_DEFAULT
    order = {s: i for i, (s, _) in enumerate(_SECTOR_RULES)}
    return max(scores, key=lambda s: (scores[s], -order[s]))


def _source_count(signals: list[dict]) -> int:
    """Distinct normalised source domains across a cluster's signals (HONEST breadth —
    many signal URLs are null, so this is intentionally conservative; the research UI
    leads with development count, not this)."""
    domains = {_source_domain(s.get("url") or "") for s in signals if s.get("url")}
    domains.discard("")
    return len(domains)


# ── Anti-hallucination: key-point grounding ───────────────────────────────────
# `key_points` are outward-facing, quotable factual claims, so they get the strictest
# rail of the synthesis outputs: (1) a data-sufficiency gate — drop them entirely on
# thin/opinion-only clusters rather than let the model extrapolate; (2) a numeric
# grounding check — any specific multi-digit figure a key point cites MUST appear in the
# source signal text, else the point is dropped (catches fabricated stats). This mirrors
# the digest's `_keep_bullets_only` fail-safe: never trust the model to self-censor.
MIN_GROUNDED_FOR_KEY_POINTS = 3   # need ≥3 body-bearing news/governance developments

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _signal_corpus(signals: list[dict]) -> str:
    return " ".join(f"{s.get('title') or ''} {s.get('body') or ''}" for s in signals).lower()


def _significant_numbers(text: str) -> set[str]:
    """Normalised numeric tokens with ≥2 significant digits (e.g. 292, 5.2→52, 1500).
    Single-digit numbers ('$5B', '8%') are too common to verify and are ignored."""
    out: set[str] = set()
    for m in _NUM_RE.findall(text or ""):
        digits = re.sub(r"[,.]", "", m).lstrip("0") or "0"
        if len(digits) >= 2:
            out.add(digits)
    return out


def _ground_key_points(key_points: list[str], signals: list[dict]) -> list[str]:
    """Return only the key points that are sufficiently grounded; [] on thin data."""
    if not key_points:
        return []
    grounded = [s for s in signals
                if (s.get("body") or "").strip() and s.get("signal_type") in ("news", "governance")]
    if len(grounded) < MIN_GROUNDED_FOR_KEY_POINTS:
        log.debug("narratives: dropping all key_points — only %d grounded development(s)", len(grounded))
        return []
    corpus_nums = _significant_numbers(_signal_corpus(signals))
    kept: list[str] = []
    for kp in key_points:
        nums = _significant_numbers(kp)
        if nums and not nums.issubset(corpus_nums):
            log.debug("narratives: dropping ungrounded key_point (figure absent from sources): %s", kp[:90])
            continue
        kept.append(kp)
    return kept[:3]


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Entity resolution ────────────────────────────────────────────────────────
class _EntityMatcher:
    """Word-boundary matcher: signal text → set of entity slugs."""

    def __init__(self) -> None:
        # (compiled_pattern, slug)
        self.patterns: list[tuple[re.Pattern, str]] = []
        self.name_by_slug: dict[str, str] = {}
        try:
            for e in db.get_entities_for_matching():
                slug = e["slug"]
                self.name_by_slug[slug] = e["name"]
                terms = [e["name"], *(e.get("aliases") or [])]
                type_ = e.get("type")
                mentions = e.get("mention_count") or 0
                for term in terms:
                    t = (term or "").strip()
                    # Shared gate kills generic vocabulary ("yield", "vault", …) so
                    # it can never tag an unrelated entity (see entities.GENERIC_TERMS).
                    if not entities.matchable_term(t, type_, mentions):
                        continue
                    esc = re.escape(t)
                    self.patterns.append((re.compile(r"\b" + esc + r"\b", re.I), slug))
        except Exception:
            log.warning("narratives: entity matcher load failed", exc_info=True)

    def match(self, text: str) -> list[str]:
        if not text:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for pat, slug in self.patterns:
            if slug in seen:
                continue
            if pat.search(text):
                seen.add(slug)
                out.append(slug)
        return out


# ── Signal gathering ─────────────────────────────────────────────────────────
def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (t or "").lower())).strip()


def _digest_url_map(start: date_t, end: date_t) -> dict[str, str]:
    """Map normalized bullet title → source URL, parsed from stored digests."""
    out: dict[str, str] = {}
    try:
        from .digest import _parse_digest_bullets  # lazy: avoid import cycle
        rows = db.get_digests_for_range(start, end)
        for _d, content in rows:
            for b in _parse_digest_bullets(content or ""):
                if b.get("link"):
                    out[_norm_title(b["title"])] = b["link"]
    except Exception:
        log.debug("narratives: digest URL map failed", exc_info=True)
    return out


def _gather_signals(matcher: _EntityMatcher, days: int, ref: datetime) -> list[dict]:
    signals: list[dict] = []
    start = ref.date() - timedelta(days=days)

    # — News (digest bullets) —
    url_map = _digest_url_map(start, ref.date())
    try:
        for r in db.get_bullet_analyses_window(days):
            title = r["title"] or ""
            # Skip meta-signals (roundups, recaps, digests) — they summarise many events
            # and contain too many entity mentions, which distorts cluster centroids and
            # pollutes evidence lists with off-topic "evidence".
            if _META_SIGNAL_RE.search(title):
                log.debug("narratives: skipping meta-signal '%s'", title)
                continue
            body = r.get("body") or ""
            text = f"{title} {body}".strip()
            ts = datetime.combine(r["digest_date"], time(9, 0), tzinfo=timezone.utc)
            signals.append({
                "signal_type": "news",
                "signal_ref": f"bull:{r['digest_date']}:{_slugify(title)[:48]}",
                "title": title, "body": body, "text": text,
                "url": url_map.get(_norm_title(title)),
                "importance": r.get("importance_score"),
                "ts": ts,
                "entities": matcher.match(text),
                "severity": _severity(text),
            })
    except Exception:
        log.warning("narratives: news gather failed", exc_info=True)

    # — Podcast (claims + predictions) —
    try:
        for ep in db.get_podcast_summaries_window(days):
            a = ep.get("analysis") or {}
            claims = (a.get("notable_claims") or []) + (a.get("predictions") or [])
            claims = [c for c in claims if isinstance(c, str) and c.strip()][:4]
            if not claims:
                continue
            body = " · ".join(claims)
            title = (a.get("tldr") or ep.get("title") or "").strip()[:180]
            text = f"{title} {body}".strip()
            pub = ep.get("published_at")
            ts = util.as_utc(pub if isinstance(pub, datetime) else ref)
            signals.append({
                "signal_type": "podcast",
                "signal_ref": f"pod:{ep['video_id']}",
                "title": title or f"{ep.get('channel','Podcast')} episode",
                "body": body, "text": text,
                "url": ep.get("url"),
                "importance": None,
                "ts": ts,
                "entities": matcher.match(text),
                "severity": _severity(text),
            })
    except Exception:
        log.warning("narratives: podcast gather failed", exc_info=True)

    # — Kaiko Research (direct from feed_items) —
    # Kaiko publishes analysis/research, not event-driven news, so most articles never
    # become digest bullets. We pull them here so Kaiko thematic intelligence reaches
    # the Research layer regardless of the event-driven digest filter. Default importance
    # reflects credibility-3.0 premium status without overshadowing multi-source bullets.
    _KAIKO_DEFAULT_IMPORTANCE = 70
    try:
        for r in db.get_kaiko_feed_signals(days):
            title = (r.get("title") or "").strip()
            if not title:
                continue
            if _META_SIGNAL_RE.search(title):
                log.debug("narratives: skipping Kaiko meta-signal '%s'", title)
                continue
            # Trim body to signal length — enough for entity matching and embedding
            body = (r.get("content") or "")[:800]
            text = f"{title} {body}".strip()
            pub = r.get("ts")
            ts = util.as_utc(pub if isinstance(pub, datetime) else ref)
            signals.append({
                "signal_type": "news",
                "signal_ref": f"kaiko:{_slugify(title)[:48]}",
                "title": title, "body": body, "text": text,
                "url": r.get("link"),
                "importance": _KAIKO_DEFAULT_IMPORTANCE,
                "ts": ts,
                "entities": matcher.match(text),
                "severity": _severity(text),
            })
    except Exception:
        log.warning("narratives: Kaiko feed gather failed", exc_info=True)

    # — Governance — (disabled by default: see INCLUDE_GOVERNANCE)
    if INCLUDE_GOVERNANCE:
        try:
            for p in db.get_governance_signals_window(max(days, 21)):
                title = p.get("title") or ""
                space = p.get("space_name") or ""
                text = f"{space} {title}".strip()
                st = p.get("start_ts")
                ts = util.as_utc(st if isinstance(st, datetime) else ref)
                ents = matcher.match(text)
                # tie the proposal to its space's entity even if the name isn't in the text
                ents = list(dict.fromkeys(ents + matcher.match(space)))
                signals.append({
                    "signal_type": "governance",
                    "signal_ref": f"gov:{p['proposal_id']}",
                    "title": f"{space}: {title}" if space else title,
                    "body": "", "text": text,
                    "url": f"https://snapshot.org/#/{p.get('space_id','')}/proposal/{p.get('proposal_id','')}",
                    "importance": None,
                    "ts": ts,
                    "entities": ents,
                    "severity": "gold",
                })
        except Exception:
            log.warning("narratives: governance gather failed", exc_info=True)

    return signals


def _mass(sig: dict) -> float:
    imp = sig.get("importance")
    if imp is not None:
        return max(0.0, min(100.0, float(imp))) / 100.0
    return DEFAULT_MASS.get(sig["signal_type"], 0.5)


# ── Clustering ───────────────────────────────────────────────────────────────
def _embed_signals(signals: list[dict]) -> None:
    """Attach a 768-dim vector to each signal (best-effort, parallel)."""
    def _one(s: dict) -> None:
        try:
            s["vec"] = embeddings.embed(embeddings.clean_for_embedding(s["text"]) or s["text"])
        except Exception:
            s["vec"] = None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(_one, signals))
    except Exception:
        log.warning("narratives: embedding pass failed", exc_info=True)
        for s in signals:
            s.setdefault("vec", None)


def _narrow(entities: list[str]) -> set[str]:
    return {e for e in entities if e not in BROAD_ENTITIES}


def _cluster(signals: list[dict]) -> list[dict]:
    """Greedy online clustering. Entity overlap is primary; cosine supports/breaks ties."""
    # Seed around the most important signals first for stable cluster cores.
    ordered = sorted(signals, key=lambda s: (_mass(s), s["ts"]), reverse=True)
    clusters: list[dict] = []

    for s in ordered:
        s_narrow = _narrow(s["entities"])
        best, best_score = None, 0.0
        for c in clusters:
            overlap = len(s_narrow & c["narrow_entities"])
            cos = _cosine(s.get("vec"), c.get("centroid"))
            merge = (
                overlap >= 2
                or (overlap >= 1 and cos >= COSINE_SUPPORT)
                or (cos >= COSINE_STRONG)
            )
            if not merge:
                continue
            score = overlap * 1.0 + cos
            if score > best_score:
                best, best_score = c, score
        if best is None:
            best = {
                "signals": [], "centroid": None, "n_vec": 0,
                "entities": [], "narrow_entities": set(), "entity_freq": {},
            }
            clusters.append(best)
        # add signal
        best["signals"].append(s)
        for e in s["entities"]:
            best["entity_freq"][e] = best["entity_freq"].get(e, 0) + 1
        best["narrow_entities"] |= s_narrow
        # running-mean centroid
        v = s.get("vec")
        if v:
            if best["centroid"] is None:
                best["centroid"] = list(v)
                best["n_vec"] = 1
            else:
                n = best["n_vec"]
                best["centroid"] = [(c0 * n + v0) / (n + 1) for c0, v0 in zip(best["centroid"], v)]
                best["n_vec"] = n + 1
    # Keep only clusters that are both corroborated (≥ MIN_SIGNALS) AND temporally
    # spread (signals on ≥ MIN_SPAN_DAYS distinct days) — the latter is what separates
    # a persistent narrative from a single news day that happened to spawn 3 bullets.
    def _distinct_days(c: dict) -> int:
        return len({s["ts"].date() for s in c["signals"]})
    return [c for c in clusters
            if len(c["signals"]) >= MIN_SIGNALS and _distinct_days(c) >= MIN_SPAN_DAYS]


def _merge_into(primary: dict, secondary: dict) -> None:
    """Absorb secondary's signals, entities, and centroid into primary in-place."""
    primary["signals"].extend(secondary["signals"])
    for e, cnt in secondary["entity_freq"].items():
        primary["entity_freq"][e] = primary["entity_freq"].get(e, 0) + cnt
    primary["narrow_entities"] |= secondary["narrow_entities"]
    n1, n2 = primary.get("n_vec", 0), secondary.get("n_vec", 0)
    if primary.get("centroid") and secondary.get("centroid") and n1 + n2 > 0:
        primary["centroid"] = [
            (a * n1 + b * n2) / (n1 + n2)
            for a, b in zip(primary["centroid"], secondary["centroid"])
        ]
        primary["n_vec"] = n1 + n2


def _consolidate(clusters: list[dict]) -> list[dict]:
    """Merge clusters that are the same story split across ingestion batches.

    Requires BOTH ≥2 shared narrow entities AND centroid cosine ≥0.70 — entity overlap
    alone is not enough because popular protocols (morpho, optimism) appear across many
    distinct stories. The cosine gate ensures the stories are genuinely about the same thing.
    """
    COSINE_CONSOLIDATE = 0.70
    ordered = sorted(clusters, key=lambda c: len(c["signals"]), reverse=True)
    accepted: list[dict] = []
    for c in ordered:
        absorbed = False
        for a in accepted:
            shared = len(c["narrow_entities"] & a["narrow_entities"])
            cos = _cosine(c.get("centroid"), a.get("centroid"))
            if shared >= 2 and cos >= COSINE_CONSOLIDATE:
                _merge_into(a, c)
                absorbed = True
                break
        if not absorbed:
            accepted.append(c)
    return accepted


def _recompute_centroid(c: dict) -> None:
    """Recompute centroid from all embedded signals (not the greedy running mean).

    The greedy mean built during clustering weights early, high-importance seeds heavily.
    A full recompute gives a centroid representative of the MAJORITY of signals, which
    makes the subsequent pruning pass far more accurate.
    """
    vecs = [s["vec"] for s in c["signals"] if s.get("vec")]
    if not vecs:
        return
    dim = len(vecs[0])
    centroid = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
    c["centroid"] = centroid
    c["n_vec"] = len(vecs)


def _prune_signals(clusters: list[dict]) -> list[dict]:
    """Recompute each cluster's centroid then drop signals too dissimilar to it.

    Two-pass approach:
      1. Recompute centroid over ALL signals (fixes running-mean insertion-order bias).
      2. Drop any signal whose cosine to that centroid is below MIN_SIGNAL_RELEVANCE.
    Only signals with valid vectors are pruned; entity-overlap signals without embeddings
    are kept unconditionally (entity overlap is the stronger inclusion signal).
    """
    result = []
    for c in clusters:
        _recompute_centroid(c)
        centroid = c.get("centroid")
        if not centroid:
            result.append(c)
            continue
        kept = [s for s in c["signals"]
                if s.get("vec") is None
                or _cosine(s.get("vec"), centroid) >= MIN_SIGNAL_RELEVANCE]
        dropped = len(c["signals"]) - len(kept)
        if dropped:
            log.debug("narratives: pruned %d weak signal(s) from cluster (centroid relevance < %.2f)",
                      dropped, MIN_SIGNAL_RELEVANCE)
            c["signals"] = kept
            c["entity_freq"] = {}
            c["narrow_entities"] = set()
            for s in kept:
                for e in s["entities"]:
                    c["entity_freq"][e] = c["entity_freq"].get(e, 0) + 1
                c["narrow_entities"] |= _narrow(s["entities"])
        result.append(c)
    return result


def _apply_entity_cap(clusters: list[dict],
                      cap: int = MAX_CLUSTERS_PER_ENTITY) -> list[dict]:
    """Board diversity: at most `cap` narratives per dominant entity.

    Dominant entity priority:
      1. Highest-frequency entity in SEMI_BROAD (versioned protocols)
      2. Highest-frequency non-broad entity that appears in ≥40% of signals
    When the cap fires, all smaller clusters are merged into the largest so no
    signals are lost — the surviving narrative gets a richer synthesis input.
    """
    def _root(c: dict) -> str | None:
        # Cap on SEMI_BROAD versioned protocol entities (aave-v4, uniswap-v3…).
        semi = {e: f for e, f in c["entity_freq"].items() if e in SEMI_BROAD}
        if semi:
            return max(semi, key=semi.get)
        # Also cap on DOMINANT_PROTOCOLS (aave/morpho/pendle) — but ONLY when the protocol
        # is the cluster's protagonist (in ≥60% of its signals). Capping on a protocol that
        # merely appears across distinct stories would over-merge unrelated narratives.
        n = max(1, len(c["signals"]))
        dom = {e: f for e, f in c["entity_freq"].items()
               if e in DOMINANT_PROTOCOLS and f / n >= DOMINANT_PROTAGONIST_FRAC}
        if dom:
            return max(dom, key=dom.get)
        return None

    by_root: dict[str, list[dict]] = {}
    no_root: list[dict] = []
    for c in clusters:
        r = _root(c)
        if r:
            by_root.setdefault(r, []).append(c)
        else:
            no_root.append(c)

    result: list[dict] = list(no_root)
    for root, group in by_root.items():
        if len(group) <= cap:
            result.extend(group)
        else:
            # Merge all into the one with the most signals
            group.sort(key=lambda c: len(c["signals"]), reverse=True)
            primary = group[0]
            for secondary in group[1:]:
                _merge_into(primary, secondary)
                log.debug("narratives: entity-cap merge — %d signals absorbed into primary"
                          " (root=%s)", len(secondary["signals"]), root)
            result.append(primary)
    return result


# ── Momentum ─────────────────────────────────────────────────────────────────
def _momentum(cluster: dict, ref: datetime) -> dict:
    sigs = cluster["signals"]
    R = 0.0
    older = 0.0
    delta = 0
    for s in sigs:
        age_h = (ref - s["ts"]).total_seconds() / 3600.0
        m = _mass(s)
        if age_h <= 48:
            R += m
            delta += 1
        elif age_h <= BASELINE_HOURS:
            older += m
    # Normalise the (48h, BASELINE_HOURS] baseline to a 48h-equivalent so ρ compares
    # the recent burst against the sustained multi-week level, not just the prior 5 days.
    B = older / max(1.0, (BASELINE_HOURS - 48) / 48.0)
    rho = (R + 1.0) / (B + 1.0)

    ts_all = [s["ts"] for s in sigs]
    first_ts, last_ts = min(ts_all), max(ts_all)
    age_h = (ref - first_ts).total_seconds() / 3600.0
    last_age_h = (ref - last_ts).total_seconds() / 3600.0
    n = len(sigs)

    if last_age_h > 168:
        state = "dormant"
    elif age_h <= 96 and n <= 4:
        # ≤96h / n≤4 (was 72h / 3): a cluster needs ≥3 signals across ≥2 distinct days to
        # exist at all, so the old window made "forming" a near-empty state (0 on the board
        # for weeks, measured 2026-07-07).
        state = "forming"
    elif rho >= RHO_HEATING and R >= R_MIN:
        state = "heating"
    elif (R == 0 and B >= COOLING_BASELINE_MIN) or rho <= 0.7:
        state = "cooling"
    else:
        state = "steady"

    # Source diversity gate (anti-manipulation): a narrative driven by a single source
    # domain with < 3 signals in the last 48h can't justify an ELEVATED momentum state —
    # one prolific account shouldn't be able to manufacture a "heating"/"forming" story.
    # It is NOT grounds for dormancy, though: a well-corroborated narrative whose last
    # signal landed a day ago is genuinely live, just not hot. Cap it at "steady" (stays
    # on the board); only the recency rule above (last signal > 168h) marks it dormant.
    distinct_domains = len({
        _source_domain(s.get("url") or "")
        for s in cluster["signals"]
        if s.get("url")
    })
    if distinct_domains <= 1 and delta < 3 and state in ("heating", "forming"):
        log.debug(
            "narratives: capping momentum at steady — only %d source domain(s), %d recent signals",
            distinct_domains, delta,
        )
        state = "steady"

    return {
        "intensity_48h": round(R, 3), "baseline": round(B, 3),
        "momentum_ratio": round(rho, 3), "delta_48h": delta,
        "state": state, "first_seen": first_ts.date(), "last_signal_at": last_ts,
        "signal_count": n,
    }


def _dominant_type(cluster: dict) -> str:
    tally: dict[str, float] = {}
    for s in cluster["signals"]:
        tally[s["signal_type"]] = tally.get(s["signal_type"], 0.0) + _mass(s)
    return max(tally, key=tally.get) if tally else "news"


def _top_entities(cluster: dict, limit: int = 6) -> list[str]:
    freq = cluster["entity_freq"]
    # prefer non-broad, then by frequency
    ranked = sorted(freq.items(), key=lambda kv: (kv[0] not in BROAD_ENTITIES, kv[1]), reverse=True)
    return [slug for slug, _ in ranked[:limit]]


# ── Synthesis (label + thesis) ───────────────────────────────────────────────
def _heuristic_label(entity_names: list[str], cluster: dict) -> str:
    if entity_names:
        return " · ".join(entity_names[:2])
    # fall back to the highest-mass signal's title (trimmed)
    top = max(cluster["signals"], key=_mass)
    return (top["title"] or "Emerging signal")[:48]


def _synthesize(signals: list[dict], entity_names: list[str],
                tvl_rows: list[dict] | None = None) -> dict:
    """LLM label + thesis + watch_next + contrarian. Returns {} on failure."""
    try:
        user = prompts.build_narrative_synthesis_user(signals, entity_names, tvl_rows)
        content, model = llm.complete(prompts.NARRATIVE_SYNTHESIS_SYSTEM, user,
                                      max_tokens=750, temperature=0.3, json_mode=True)
        data = llm.parse_json_loose(content)
        label = (data.get("label") or "").strip()
        if not label:
            return {}
        if _looks_like_person(label):
            log.warning("narratives: synthesis returned a person name as label (%r) — discarding", label)
            return {}
        wn = data.get("watch_next") or []
        wn = [w.strip() for w in wn if isinstance(w, str) and w.strip()][:3]
        kp = data.get("key_points") or []
        kp = [k.strip() for k in kp if isinstance(k, str) and k.strip()][:3]
        return {
            "label": label[:80],
            "thesis": (data.get("thesis") or "").strip(),
            "key_points": kp,
            "watch_next": wn,
            "contrarian": (data.get("contrarian") or "").strip() or None,
            "model_used": model,
        }
    except Exception:
        log.debug("narratives: synthesis failed", exc_info=True)
        return {}


# ── Public entry point ───────────────────────────────────────────────────────
def build_and_store(days: int = WINDOW_DAYS, persist: bool = True,
                    use_llm: bool = True, ref_date: date_t | None = None) -> dict:
    """Rebuild the narrative layer. Returns stats. Never raises on inner failures."""
    ref = datetime.now(timezone.utc)
    if ref_date and ref_date < ref.date():
        ref = datetime.combine(ref_date, time(9, 0), tzinfo=timezone.utc)

    matcher = _EntityMatcher()
    signals = _gather_signals(matcher, days, ref)
    if not signals:
        log.info("narratives: no signals in window — nothing to build")
        if persist:
            try:
                db.replace_narratives([], {})
            except Exception:
                log.warning("narratives: clear failed", exc_info=True)
        return {"signals": 0, "narratives": 0}

    _embed_signals(signals)
    raw_clusters = _cluster(signals)

    # Drop signals too dissimilar to their cluster centroid, then re-apply
    # the corroboration + span gates (pruning may push a cluster below MIN_SIGNALS).
    def _span_days(c: dict) -> int:
        return len({s["ts"].date() for s in c["signals"]})

    pruned = _prune_signals(raw_clusters)
    pruned = [c for c in pruned
              if len(c["signals"]) >= MIN_SIGNALS and _span_days(c) >= MIN_SPAN_DAYS]

    clusters = _apply_entity_cap(pruned)
    log.info(
        "narratives: %d signals → %d raw clusters → %d after pruning → %d after entity-diversity cap",
        len(signals), len(raw_clusters), len(pruned), len(clusters),
    )

    # Existing narratives for label/thesis reuse (avoid re-LLM on unchanged clusters)
    existing = []
    try:
        existing = db.get_existing_narratives()
    except Exception:
        log.debug("narratives: could not load existing for reuse", exc_info=True)

    def _match_existing(entity_slugs: list[str], n: int) -> dict | None:
        es = set(entity_slugs)
        for ex in existing:
            shared = es & set(ex.get("entity_slugs") or [])
            if len(shared) >= 2 and abs((ex.get("signal_count") or 0) - n) <= 2 and ex.get("thesis"):
                return ex
        return None

    # Build narrative records; decide which need fresh synthesis.
    records: list[dict] = []
    to_synth: list[int] = []
    for c in clusters:
        mom = _momentum(c, ref)
        top_slugs = _top_entities(c)
        entity_names = [matcher.name_by_slug.get(s, s) for s in top_slugs]
        top_sig = max(c["signals"], key=_mass)
        rec = {
            "entity_slugs": top_slugs,
            "centroid": c.get("centroid"),
            "dominant_type": _dominant_type(c),
            "severity": top_sig["severity"],
            "source_count": _source_count(c["signals"]),
            **mom,
            "_signals": c["signals"],
            "_entity_names": entity_names,
        }
        reuse = _match_existing(top_slugs, mom["signal_count"])
        if reuse:
            rec.update({
                "slug": reuse["slug"], "label": reuse["label"], "thesis": reuse["thesis"],
                "key_points": reuse.get("key_points") or [],
                "watch_next": reuse.get("watch_next") or [],
                "contrarian": reuse.get("contrarian"),
                "model_used": reuse.get("model_used") or "",
            })
        else:
            rec["label"] = _heuristic_label(entity_names, c)
            records.append(rec)
            to_synth.append(len(records) - 1)
            continue
        records.append(rec)

    # Live DeFiLlama TVL/flows for every referenced entity — fetched ONCE here and reused for
    # both synthesis grounding (the thesis is about where capital moves) and the sector votes
    # below. Best-effort: synthesis still runs on signal text alone if this fails.
    prot_by_slug: dict[str, dict] = {}
    try:
        all_slugs = sorted({s for rec in records for s in (rec.get("entity_slugs") or [])})
        if all_slugs:
            prot_by_slug = {p["slug"]: p for p in db.get_protocols_by_slugs(all_slugs)}
    except Exception:
        log.debug("narratives: protocol TVL fetch failed (non-fatal)", exc_info=True)

    # LLM synthesis for new/changed clusters (capped, parallel) — most important first.
    if use_llm and to_synth:
        to_synth.sort(key=lambda i: records[i]["intensity_48h"], reverse=True)
        capped = to_synth[:MAX_LLM_SYNTHESES]
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futs = {pool.submit(_synthesize, records[i]["_signals"],
                                    records[i]["_entity_names"],
                                    [prot_by_slug[s] for s in (records[i].get("entity_slugs") or [])
                                     if s in prot_by_slug]): i for i in capped}
                for fut in as_completed(futs):
                    i = futs[fut]
                    res = fut.result()
                    if res:
                        records[i].update({
                            "label": res["label"], "thesis": res["thesis"],
                            "key_points": res["key_points"],
                            "watch_next": res["watch_next"], "contrarian": res["contrarian"],
                            "model_used": res["model_used"],
                        })
        except Exception:
            log.warning("narratives: synthesis pool failed", exc_info=True)

    # DeFiLlama categories for grounded sector votes — reuse the TVL map fetched above
    # (no second DB round-trip).
    cat_by_slug = {s: p["category"] for s, p in prot_by_slug.items() if p.get("category")}

    # Assign stable, unique slugs.
    used: set[str] = set()
    signals_by_slug: dict[str, list[dict]] = {}
    final: list[dict] = []
    for rec in records:
        slug = rec.get("slug")
        if not slug:
            base = _slugify(rec.get("label") or "-".join(rec["entity_slugs"][:3])) or "narrative"
            slug = base[:60]
        s = slug
        k = 2
        while s in used:
            s = f"{slug}-{k}"
            k += 1
        used.add(s)
        rec["slug"] = s
        cluster_signals = rec.pop("_signals")
        signals_by_slug[s] = [
            {"signal_type": x["signal_type"], "signal_ref": x["signal_ref"],
             "title": x["title"], "body": x.get("body"), "url": x.get("url"),
             "importance": x.get("importance"), "ts": x["ts"]}
            for x in cluster_signals
        ]
        rec.pop("_entity_names", None)
        rec.setdefault("watch_next", [])
        # Anti-hallucination rail: ground key_points against the cluster's own signals —
        # drops any fabricated figure and suppresses key_points entirely on thin data.
        rec["key_points"] = _ground_key_points(rec.get("key_points") or [], cluster_signals)
        rec.setdefault("contrarian", None)
        rec.setdefault("thesis", None)
        rec.setdefault("model_used", "")
        # Sector is deterministic — recompute every rebuild from the final label/thesis +
        # the entities' DeFiLlama categories (cheap, no LLM) so it tracks the synthesis.
        rec["sector"] = _sector(rec.get("label"), rec.get("thesis"),
                                rec.get("entity_slugs"), cat_by_slug)
        rec.setdefault("source_count", 0)
        final.append(rec)

    # Sort: heating/forming first, then by intensity (board order is also computed in SQL).
    state_rank = {"heating": 0, "forming": 1, "steady": 2, "cooling": 3, "dormant": 4}
    final.sort(key=lambda r: (state_rank.get(r["state"], 9), -r["intensity_48h"]))

    if persist:
        try:
            db.replace_narratives(final, signals_by_slug)
            log.info("narratives: stored %d narratives", len(final))
        except Exception:
            log.warning("narratives: persist failed", exc_info=True)

    return {"signals": len(signals), "narratives": len(final),
            "states": {st: sum(1 for r in final if r["state"] == st)
                       for st in state_rank}}


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Rebuild the narrative intelligence layer.")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS, help="signal lookback window")
    ap.add_argument("--no-persist", action="store_true", help="print clusters, don't write DB")
    ap.add_argument("--no-llm", action="store_true", help="heuristic labels only (no LLM)")
    args = ap.parse_args()

    stats = build_and_store(days=args.days, persist=not args.no_persist,
                            use_llm=not args.no_llm)
    print(json.dumps(stats, indent=2, default=str))
