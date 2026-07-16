"""Entity memory: alias-based detection and enrichment for digest/agent context.

Flow:
  ingest → extract_and_upsert_entities(new_items)   # LLM, one call per ingest cycle
  digest  → build_entity_context(feed_items)          # alias match, free
  agent   → build_entity_context([synthetic item])    # same
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone

from . import config, db, llm, prompts, util

log = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"```[a-z]*\n?", re.IGNORECASE)

_ERC_RE = re.compile(r"^(ERC|EIP|BIP|CIP|AIP|SIP|RIP)-\d+$", re.IGNORECASE)

BLOCKED_ALIASES = {
    "defi", "crypto", "web3", "governance", "proposal", "hack", "exploit",
    "chain", "layer", "protocol", "token", "yield", "bridge", "contract",
    "system", "network", "liquidity", "market", "finance", "capital", "dao",
    "other", "stablecoin", "announcement", "partnership", "integration",
    "update", "security", "vulnerability", "audit", "deposit", "withdraw",
    "transaction", "gas", "fees", "upgrade", "mainnet", "testnet"
}

# Generic crypto vocabulary that must never become a word-boundary match on its
# own (it would tag "Yield Basis" on any "yield" headline, "Layer" on any L2 post,
# etc.). Superset of BLOCKED_ALIASES used by every runtime matcher (narratives +
# entity_graph) via matchable_term(). Multi-word names are unaffected — only the
# bare single term is rejected.
GENERIC_TERMS = BLOCKED_ALIASES | {
    "basis", "season", "points", "point", "vault", "vaults", "perp", "perps",
    "perpetual", "perpetuals", "restaking", "staking", "airdrop", "rollup",
    "rollups", "wallet", "oracle", "oracles", "validator", "validators", "node",
    "nodes", "swap", "swaps", "dex", "cex", "lending", "borrow", "borrowing",
    "treasury", "etf", "etfs", "rwa", "meme", "memecoin", "presale", "mint",
    "minting", "vesting", "unlock", "unlocks", "fork", "mainnet", "incentive",
    "incentives", "reward", "rewards", "fund", "labs", "ventures", "foundation",
    "exchange", "wrapped", "native", "core", "main", "new", "open", "world",
    "onchain",
    # "notional" follows the across/strategy/public precedent: Notional-the-protocol
    # is unreachable behind derivatives coverage ("$80B notional volume") — the bare
    # word OR-matched half the corpus in scoring corroboration (2026-07-01).
    "notional",
    # "loan"/"loans" — generic word for what a lending protocol DOES, not a brand
    # identity (unlike "lending"/"borrow"/"borrowing" already above, this one was
    # missed and sat as a false-positive-prone alias on both LiquidLoans and MetalX
    # Lending — neither is "the" owner of the word, so dealias's dominance check
    # (needs a single entity to clearly own it) can't strip it either).
    "loan", "loans",
    # Common English words that are ALSO entity names — matching the bare word
    # floods the graph ("Across" protocol matched "across" 254×, "Strategy",
    # "Public"). The entity stays reachable via a distinctive alias/ticker if it
    # has one; only the ambiguous bare token is rejected.
    "across", "strategy", "public", "global", "digital", "general", "future",
    "futures", "spot", "story", "signal", "standard", "group", "prime", "pure",
    "simple", "instant", "secure", "trust", "official", "real", "fun", "free",
    "idle", "movement", "bullish", "bearish", "push", "believe", "across",
    "stable", "credit", "savings", "saving", "select", "fixed", "smart", "auto",
    # Common English words seen as junk single-word ALIASES (extraction noise) that
    # flood the matcher — e.g. "hard"→Kava Lend, "link"→Chainlink, "next"→Connext,
    # "move"→Movement. The real token (LINK, MOVE) appears all-caps and stays reachable
    # via the brand NAME (case-sensitive). NOTE: do NOT add brand names that are also
    # words (Base, Flow, Spark, Render, Strike) — those are handled by case-sensitive
    # matching, not blocked.
    "hard", "edge", "next", "move", "link", "farm", "coin", "call", "date", "deal",
    "news", "live", "wave", "gate",
}


def matchable_term(term: str, type_: str | None = None, mention_count: int = 0) -> bool:
    """Whether a name/alias may be used as a standalone word-boundary match.

    Shared by every runtime matcher so false positives are killed in ONE place:
    rejects @handles, pure digits, generic crypto vocabulary, and very short
    ambiguous tokens — except short distinctive tickers (3-5 chars) for well-known
    protocols/chains (Arc, Sui, SP1), which need ≥10 mentions to qualify.
    """
    t = (term or "").strip()
    if not t or t.startswith("@") or t.isdigit():
        return False
    low = t.lower()
    if low in GENERIC_TERMS:
        return False
    if len(t) >= 6:
        return True
    long_enough = len(t) >= 4
    short_distinct = (
        3 <= len(t) <= 5 and " " not in t
        and type_ in ("protocol", "chain", "dao", "exchange", "fund")
        and (mention_count or 0) >= 10
    )
    return long_enough or short_distinct



# ── Corpus prose gate (extraction-time prevention) ───────────────────────────
# The curated GENERIC_TERMS list can never enumerate every common word the LLM
# will mint as a junk entity ("would", "zero", "time", "cash", …). Instead of
# growing the list after each audit, a NEW entity's bare single-word identity is
# checked against the project's OWN feed corpus: if the word already appears in
# ≥40 documents (or is an English stopword the tsquery parser drops entirely),
# it is ordinary prose, not a distinctive brand — refuse to mint the entity.
# Existing entities are never re-judged (their corpus frequency IS coverage).
_PROSE_HITS_MIN = 40  # mirrors app.audit._COLLISION_MIN_HITS
_PROSE_CACHE: dict[str, bool] = {}


def _common_prose_word(term: str) -> bool:
    """True when a bare single alphabetic token reads as ordinary prose in the
    feed corpus (or is an English stopword) — not a distinctive brand identity."""
    t = (term or "").strip().lower()
    if not t or " " in t or not t.isalpha() or len(t) > 10:
        return False
    cached = _PROSE_CACHE.get(t)
    if cached is None:
        n = db.prose_doc_count(t, cap=_PROSE_HITS_MIN)
        cached = n is None or n >= _PROSE_HITS_MIN
        _PROSE_CACHE[t] = cached
    return cached


# --------------------------------------------------------------------------- #
# Ingest-time: LLM entity extraction → entity_memory upsert
# --------------------------------------------------------------------------- #

def extract_and_upsert_entities(items: list[dict]) -> int:
    """One LLM call over new feed items; upserts discovered entities to entity_memory.

    Only called when new items were actually inserted (inserted > 0) to avoid
    burning LLM calls on idle ingest cycles.
    Returns count of entities upserted.
    """
    if not items:
        return 0
    # Sort newest-first so the per-batch cap prioritises the most recent items.
    sorted_items = sorted(items, key=lambda x: x.get("pub_date") or "", reverse=True)
    texts = []
    for it in sorted_items[:60]:
        if not it.get("content"):
            continue
        snippet = util.plain_text(it.get("content", ""), 250)
        # Append @mentions parsed at ingest time so the LLM can directly map
        # entity names to their Twitter handles (e.g. "Aave → @AaveAave").
        mentions = [m for m in (it.get("mentions") or []) if m.startswith("@")][:5]
        if mentions:
            snippet += "  [mentions: " + ", ".join(mentions) + "]"
        texts.append(snippet)
    if not texts:
        return 0

    user_prompt = prompts.build_entity_extraction_user(texts)
    try:
        raw, _model = llm.complete(
            prompts.ENTITY_EXTRACTION_SYSTEM,
            user_prompt,
            max_tokens=2000, temperature=0.1,  # JSON array → no json_mode (object-only)
        )
    except Exception:
        log.warning("entity extraction LLM call failed", exc_info=True)
        return 0

    try:
        entities = llm.parse_json_loose(raw)
        if not isinstance(entities, list):
            return 0
    except (ValueError, TypeError):
        log.warning("entity extraction: invalid JSON (%.120r)", raw)
        return 0

    today = datetime.now(timezone.utc).date()
    count = 0
    # Cap to at most 15 entities to prevent DB pollution
    for ent in entities[:15]:
        slug = re.sub(r"[^a-z0-9-]", "-", (ent.get("slug") or "").strip().lower()).strip("-")
        name = (ent.get("name") or "").strip()
        type_ = (ent.get("type") or "other").strip()
        aliases = [
            a.lower().strip() for a in (ent.get("aliases") or [])
            if isinstance(a, str) and len(a.strip()) >= 3
            and not a.strip().isdigit()
            and a.lower().strip() not in GENERIC_TERMS
            and not _ERC_RE.match(a.lower().strip())
        ]
        # twitter_handle is stored separately; don't pollute aliases with @handles
        raw_handle = (ent.get("twitter_handle") or "").strip()
        twitter_handle: str | None = None
        if raw_handle and raw_handle.startswith("@"):
            twitter_handle = raw_handle
        elif raw_handle:
            twitter_handle = f"@{raw_handle}"

        if not slug or not name or len(slug) < 2:
            continue
        if type_ not in ("protocol", "chain", "fund", "person", "exchange", "dao", "other"):
            type_ = "other"
            
        # Always include slug and lowercased name as aliases; exclude @handles from aliases
        candidate_aliases = {slug, name.lower(), *[a for a in aliases if not a.startswith("@")]}
        # Filter all candidate aliases through quality gates
        filtered_aliases = []
        for a in candidate_aliases:
            a_clean = a.lower().strip()
            if (len(a_clean) >= 3
                and not a_clean.isdigit()
                and a_clean not in GENERIC_TERMS
                and not _ERC_RE.match(a_clean)):
                filtered_aliases.append(a_clean)

        if not filtered_aliases:
            continue

        # NEW entities only: refuse to mint one whose identity is ordinary prose in
        # our own corpus (the "would"/"zero"/"time" class the curated list can't
        # enumerate). Existing entities are exempt — their frequency IS coverage.
        try:
            is_new = not db.get_entities_by_slugs([slug])
        except Exception:
            is_new = False
        if is_new:
            if " " not in name and _common_prose_word(name):
                log.info("entity extraction: skipped common-prose entity %r (%s)", name, slug)
                continue
            filtered_aliases = [a for a in filtered_aliases if not _common_prose_word(a)]
            if not filtered_aliases:
                log.info("entity extraction: skipped %r — only common-prose aliases", slug)
                continue

        try:
            db.upsert_entity(slug, name, type_, filtered_aliases,
                             last_mentioned=today, twitter_handle=twitter_handle)
            count += 1
        except Exception:
            log.warning("failed to upsert entity %r", slug, exc_info=True)

    log.info("entity extraction: %d/%d entities upserted from %d items",
             count, min(len(entities), 15), len(texts))
    return count



# --------------------------------------------------------------------------- #
# Runtime: alias-match feed items → slugs
# --------------------------------------------------------------------------- #

# A single-token, purely-alphabetic, conventionally-capitalized brand name —
# Titlecase ("Exodus", "Flow") or ALL-CAPS ("AERO") — is also a candidate common
# English word, so it must be matched CASE-SENSITIVELY against the original-case text
# (the brand appears capitalized; the common-noun usage is lowercase). Mirrors
# searchEntityMemory Path 3 in web/lib/db.js so the two matchers never drift.
_AMBIG_SINGLE_RE = re.compile(r"^([A-Z][a-z]+|[A-Z]+)$")
_TITLECASE_RE = re.compile(r"^[A-Z][a-z]+$")


def _ambiguous_single_word(name: str) -> bool:
    return bool(name) and " " not in name and bool(_AMBIG_SINGLE_RE.match(name))


def _cs_matchable(name: str, type_: str, mention_count: int) -> bool:
    """Floor for case-sensitive single-word matches — mirrors web Path 3 floors."""
    n = len(name)
    if n >= 6:
        return True
    if _TITLECASE_RE.match(name) and 4 <= n <= 5:
        return True
    return (3 <= n <= 5 and (mention_count or 0) >= 6
            and type_ in ("protocol", "chain", "dao", "exchange", "fund"))


def _build_matchers() -> tuple[dict[str, str], list[tuple[re.Pattern, str]]]:
    """Build (case-insensitive alias map, case-sensitive single-word patterns).

    The bare lowercased name of a Titlecase/all-caps brand is kept OUT of the
    case-insensitive map and matched only case-sensitively, so its common-English
    -word usage ("exodus", "flow") can't leak as a false positive.
    """
    rows = db.get_all_entity_aliases()
    ci_map: dict[str, str] = {}
    cs_patterns: list[tuple[re.Pattern, str]] = []
    for slug, name, type_, aliases, _summary, mention_count in rows:
        mc = mention_count or 0
        ambiguous = _ambiguous_single_word(name)
        if ambiguous and _cs_matchable(name, type_, mc):
            cs_patterns.append((_cs_pattern(name), slug))
        if not ambiguous:
            # For ambiguous brands neither the slug nor the bare name may match
            # case-insensitively (the slug ≈ the lowercased common word) — only the
            # case-sensitive pattern above. The web matcher likewise never uses the slug.
            # Name/slug go through the SAME matchable_term gate as aliases: extraction
            # noise named a bare common word ("would", "zero", "stake") used to enter
            # the case-insensitive map ungated and tag every text containing the word.
            # Multi-word names always pass (the gate only rejects bare single tokens).
            if " " in slug or matchable_term(slug, type_, mc):
                ci_map[slug.lower()] = slug
            if " " in name or matchable_term(name, type_, mc):
                ci_map[name.lower()] = slug
        for a in (aliases or []):
            if not a or a.startswith("@"):
                continue
            # skip the bare-name alias of an ambiguous brand (governed case-sensitively)
            if ambiguous and a.lower() == name.lower():
                continue
            # Multi-word aliases are distinctive enough as-is; bare single tokens must
            # clear the shared matchable_term gate (length floors + GENERIC_TERMS).
            if " " in a.strip() and len(a) >= 3:
                ci_map[a.lower()] = slug
            elif matchable_term(a, type_, mc):
                ci_map[a.lower()] = slug
    return ci_map, cs_patterns


# ── Matcher cache ─────────────────────────────────────────────────────────────
# detect_entities_in_* is called dozens of times per digest run (once per bullet in
# bullet-analysis, once per covered-bullet title in _saturated_entities, plus threads +
# briefing). Each call used to re-query the whole entity_memory table and recompile the
# full matcher — pure redundant work. The (ci_map, cs_patterns) pair is now cached
# process-wide and rebuilt only when entity_memory changes: db.entity_generation() bumps
# on every entity write in THIS process, so the digest's own writes (ingest upserts) are
# reflected exactly. (A manual `entity_audit merge`/`dealias` runs in a separate CLI
# process; the bot's cache then self-heals on the next ingest upsert — minutes, always
# before the daily digest. Rebuild narratives/entity_graph after such edits anyway.)
# Guarded by a lock because bullet analysis runs the detector 3-wide.
_MATCHER_LOCK = threading.Lock()
_MATCHER_CACHE: dict | None = None   # {"gen": int, "ci_map": ..., "cs_patterns": ...}


def _reset_matcher_cache() -> None:
    """Drop the cached matcher. Used by tests that swap the mocked DB between calls."""
    global _MATCHER_CACHE
    with _MATCHER_LOCK:
        _MATCHER_CACHE = None


def _load_matchers() -> tuple[dict[str, str], list[tuple[re.Pattern, str]]]:
    """Return the compiled matcher, rebuilding it only when entity_memory has changed."""
    global _MATCHER_CACHE
    gen = db.entity_generation()
    cached = _MATCHER_CACHE
    if cached is not None and cached["gen"] == gen:
        return cached["ci_map"], cached["cs_patterns"]
    with _MATCHER_LOCK:
        cached = _MATCHER_CACHE
        if cached is None or cached["gen"] != gen:
            ci_map, cs_patterns = _build_matchers()
            cached = {"gen": gen, "ci_map": ci_map, "cs_patterns": cs_patterns}
            _MATCHER_CACHE = cached
        return cached["ci_map"], cached["cs_patterns"]


# Pre-compiled word-boundary patterns are cached to avoid recompiling per cycle.
_ALIAS_PATTERN_CACHE: dict[str, re.Pattern] = {}
_CS_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _alias_pattern(alias: str) -> re.Pattern:
    pat = _ALIAS_PATTERN_CACHE.get(alias)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(alias) + r"\b")
        _ALIAS_PATTERN_CACHE[alias] = pat
    return pat


def _cs_pattern(name: str) -> re.Pattern:
    """Case-SENSITIVE word-boundary pattern matching the name as-stored OR all-caps."""
    pat = _CS_PATTERN_CACHE.get(name)
    if pat is None:
        pat = re.compile(r"\b(" + re.escape(name) + "|" + re.escape(name.upper()) + r")\b")
        _CS_PATTERN_CACHE[name] = pat
    return pat


def detect_entities_in_items(items: list[dict]) -> list[str]:
    """Return slugs of entities mentioned in the given feed items (alias matching).

    Uses word-boundary regex to avoid substring false positives (e.g. 'free' matching
    'freeze'). Single-word Titlecase/all-caps brand names are matched case-sensitively
    so common-English-word usage ('exodus', 'flow') is not falsely tagged.
    """
    ci_map, cs_patterns = _load_matchers()
    if not ci_map and not cs_patterns:
        return []
    found: set[str] = set()
    for item in items:
        text = util.plain_text(item.get("content", ""), 300)
        low = text.lower()
        for alias, slug in ci_map.items():
            if len(alias) >= 3 and _alias_pattern(alias).search(low):
                found.add(slug)
        for pat, slug in cs_patterns:
            if pat.search(text):
                found.add(slug)
    return sorted(found)


def detect_entities_in_text(text: str) -> list[str]:
    """Detect entity slugs in a single text string (for agent queries)."""
    return detect_entities_in_items([{"content": text}])


# --------------------------------------------------------------------------- #
# Context block builder (used by digest + agent)
# --------------------------------------------------------------------------- #

def build_entity_context(items: list[dict],
                         max_entities: int | None = None) -> str:
    """Alias-match feed items, enrich with TVL + analyst summary, return context block.

    Returns empty string if entity_memory is empty or no entities matched.
    """
    max_entities = max_entities or config.ENTITY_CONTEXT_LIMIT
    slugs = detect_entities_in_items(items)
    if not slugs:
        return ""

    entity_rows = {r["slug"]: r for r in db.get_entities_by_slugs(slugs)}
    try:
        tvl_map = {r["slug"]: r for r in db.get_protocols_by_slugs(slugs)}
    except Exception:
        log.debug("could not fetch protocol TVL for entity context", exc_info=True)
        tvl_map = {}

    lines: list[str] = []
    for slug in slugs[:max_entities]:
        entity = entity_rows.get(slug)
        if not entity:
            continue
        name = entity.get("name") or slug
        summary = (entity.get("summary") or "").strip()
        tvl_info = tvl_map.get(slug)

        parts: list[str] = [name]
        if tvl_info:
            tvl = tvl_info.get("tvl_usd") or 0
            chg = tvl_info.get("tvl_change_7d")
            cat = tvl_info.get("category") or ""
            chg_str = f" ({chg:+.1f}% 7d)" if chg is not None else ""
            parts.append(f"TVL {util.fmt_usd(float(tvl))}{chg_str}")
            if cat:
                parts.append(cat)
        if summary:
            # entity_memory.summary is written by the analyst-extraction LLM pass —
            # earlier model output, not source data. Label it so the receiving prompt
            # can't launder a prior guess into today's digest as verified background.
            parts.append(f"state (unverified prior note): {summary}")

        lines.append("  " + " | ".join(parts))

    if not lines:
        return ""
    return (
        "ENTITY CONTEXT (auto-detected from today's feed). TVL figures are live "
        "DeFiLlama data — verified. Any 'state (unverified prior note)' is Horyon's "
        "own earlier MODEL-GENERATED summary: background hint only — never quote its "
        "figures, dates, or events as fact, and never report it as today's news.\n"
        + "\n".join(lines)
    )


def build_entity_context_for_query(query: str) -> str:
    """Build entity context for a single agent query string."""
    return build_entity_context([{"content": query}])
