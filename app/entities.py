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
from datetime import datetime, timezone

from . import config, db, llm, prompts

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
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
    # Common English words that are ALSO entity names — matching the bare word
    # floods the graph ("Across" protocol matched "across" 254×, "Strategy",
    # "Public"). The entity stays reachable via a distinctive alias/ticker if it
    # has one; only the ambiguous bare token is rejected.
    "across", "strategy", "public", "global", "digital", "general", "future",
    "futures", "spot", "story", "signal", "standard", "group", "prime", "pure",
    "simple", "instant", "secure", "trust", "official", "real", "fun", "free",
    "idle", "movement", "bullish", "bearish", "push", "believe", "across",
    "stable", "credit", "savings", "saving", "select", "fixed", "smart", "auto",
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



def _plain(text: str, limit: int = 300) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()[:limit]


def _fmt_usd(usd: float) -> str:
    if usd >= 1e12:
        return f"${usd / 1e12:.2f}T"
    if usd >= 1e9:
        return f"${usd / 1e9:.1f}B"
    if usd >= 1e6:
        return f"${usd / 1e6:.0f}M"
    return f"${usd:,.0f}"


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
        snippet = _plain(it.get("content", ""), 250)
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

def _load_alias_map() -> dict[str, str]:
    """Return {alias_lower → slug} for all entity_memory rows."""
    rows = db.get_all_entity_aliases()
    alias_map: dict[str, str] = {}
    for slug, name, _type, aliases, _summary in rows:
        alias_map[slug.lower()] = slug
        alias_map[name.lower()] = slug
        for a in (aliases or []):
            if a and len(a) >= 3 and not a.startswith("@"):
                alias_map[a.lower()] = slug
    return alias_map


# Pre-compiled word-boundary patterns are cached per alias to avoid
# recompiling on every ingest cycle.
_ALIAS_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _alias_pattern(alias: str) -> re.Pattern:
    pat = _ALIAS_PATTERN_CACHE.get(alias)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(alias) + r"\b")
        _ALIAS_PATTERN_CACHE[alias] = pat
    return pat


def detect_entities_in_items(items: list[dict]) -> list[str]:
    """Return slugs of entities mentioned in the given feed items (alias matching).

    Uses word-boundary regex to avoid substring false positives
    (e.g. 'free' matching 'freeze', 'meta' matching 'MetaDAO').
    """
    alias_map = _load_alias_map()
    if not alias_map:
        return []
    found: set[str] = set()
    for item in items:
        text = _plain(item.get("content", "")).lower()
        for alias, slug in alias_map.items():
            if len(alias) >= 3 and _alias_pattern(alias).search(text):
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
            chg = tvl_info.get("tvl_change_1d")
            cat = tvl_info.get("category") or ""
            chg_str = f" ({chg:+.1f}% 1d)" if chg is not None else ""
            parts.append(f"TVL {_fmt_usd(float(tvl))}{chg_str}")
            if cat:
                parts.append(cat)
        if summary:
            parts.append(summary)

        lines.append("  " + " | ".join(parts))

    if not lines:
        return ""
    return "ENTITY CONTEXT (auto-detected from today's feed):\n" + "\n".join(lines)


def build_entity_context_for_query(query: str) -> str:
    """Build entity context for a single agent query string."""
    return build_entity_context([{"content": query}])
