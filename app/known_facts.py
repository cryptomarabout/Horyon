"""Curated, human-authored ground-truth facts about entities the LLM pipeline keeps getting
wrong, injected into the digest / bullet-analysis / thread prompts as AUTHORITATIVE context.

Why this exists
---------------
Some recurring factual errors are NOT in the source items — they are introduced by the model
collapsing nuance. The motivating case (2026-06): Circle's **Arc** is an UNRELEASED testnet, and
the feeds carry day-one *support announcements* ("Uniswap is coming to Arc", "Aerodrome is coming
to Arc", "Arc Testnet is upgrading"). The pipeline kept rewriting those as live deployments
("Uniswap deploys on the Arc chain"), and one analysis even mislabelled Arc as a "prediction-market
protocol". No amount of source trust fixes this — the fix is to hand the model an authoritative,
dated fact that OVERRIDES source spin and its own prior (LLM-written) ``entity_memory.summary``.

These facts are never written by the LLM, so they cannot be hallucinated or clobbered by the
analyst extractor (which rewrites ``entity_memory.summary`` every digest). Add an entry when a
source-vs-reality gap causes a *recurring* error; keep each fact short, dated, and falsifiable;
prune it when it goes stale (e.g. once Arc mainnet ships, replace the Arc fact or drop it).
"""
from __future__ import annotations

import re

# Canonical entity slug -> authoritative fact line (kept tight; the model reads several of these).
KNOWN_FACTS: dict[str, str] = {
    "arc": (
        "Circle's Arc is an UPCOMING stablecoin-native L1 blockchain. As of June 2026 it is in "
        "PUBLIC TESTNET only — mainnet is NOT yet live. Reports that protocols (Uniswap, "
        "Aerodrome, Aave, Morpho, etc.) are 'coming to' / 'deploying on' / 'expanding to' / "
        "'migrating to' Arc are day-one SUPPORT ANNOUNCEMENTS for the future mainnet, NOT live "
        "deployments. Never state Arc as a live or operational chain, and never describe Arc as "
        "anything other than Circle's L1 (it is NOT a prediction-market or DeFi protocol)."
    ),
    # Prevent re-extraction of Anthropic AI model names as crypto entities.
    # "Fable 5" and "Mythos 5" are Anthropic Claude model names that share names with obscure
    # crypto tokens. When these models appear in crypto news coverage, the entity extractor
    # must NOT create/update crypto entities for them.
    "fable": (
        "Fable 5 (also Fable) is an Anthropic AI language model (Claude family), NOT a crypto "
        "protocol or DeFi project. Do NOT extract it as a crypto entity. Do not report on Fable "
        "AI model news as a crypto market event."
    ),
    "mythos-ai": (
        "Mythos 5 is an Anthropic AI language model (Claude family). It is distinct from Mythos "
        "Chain (MYTH token), which is a real blockchain gaming platform. When 'Mythos 5' appears "
        "in a headline, it refers to the AI model, NOT the gaming chain. Do NOT alias 'Mythos 5' "
        "or 'Mythos v5' to the Mythos Chain crypto entity."
    ),
    # Positive correction (NOT a pre-launch fact — base is in ESTABLISHED_MAINNET). The feeds carry
    # 'Beryl upgrade on Sepolia testnet' items that the pipeline kept collapsing into "Base is in
    # testnet / not yet live". Base the CHAIN is live mainnet; only the upgrade is on testnet.
    "base": (
        "Base is a LIVE Ethereum layer-2 mainnet (Coinbase's OP-Stack rollup), in production since "
        "2023. News of a 'Beryl upgrade', a 'B20 token standard', or features 'on Sepolia testnet' "
        "are TESTNET developments of an UPCOMING upgrade — they do NOT mean Base itself is in "
        "testnet or not yet live. Always describe Base as a live mainnet chain; describe the upgrade "
        "or feature as upcoming (in testnet, ahead of a future mainnet rollout)."
    ),
    # Positive correction (NOT a pre-launch fact — robinhood-chain is in ESTABLISHED_MAINNET).
    # Motivating incident (2026-07-20): Robinhood Chain launched on mainnet 2026-07-01, but its
    # OWN pre-launch coverage from late June ("Robinhood is launching its own public blockchain",
    # hackathon prize copy) keeps leaking into the model's background prose. When a fresh bullet
    # (Arcus hitting $17M TVL) needed elaboration, both the digest_bullet_analysis text AND the
    # standard/explainer audio scripts independently described the chain as something Robinhood
    # "has signaled plans to expand" into / "will use" in the future — the OPPOSITE tense error
    # from Arc (a live chain downgraded to pre-launch, not a pre-launch chain upgraded to live).
    "robinhood-chain": (
        "Robinhood Chain is a LIVE Ethereum layer-2 mainnet (an Arbitrum-based rollup) — it went "
        "live on mainnet 2026-07-01 and is NOT a planned, upcoming, or future network. Apps already "
        "trading on it (Arcus, Lighter, Uniswap v2/v3/v4, the prediction market World, and others) "
        "are LIVE deployments happening today, not announcements of future intent. Never describe "
        "Robinhood Chain as something Robinhood 'plans to', 'has signaled', 'has said it will', or "
        "'intends to' launch/expand into — say it IS live and describe what is currently running "
        "on it."
    ),
}

# Surface terms that should trigger each fact when they appear (word-boundary) in raw text.
# Used by ``facts_for_text`` where we don't already have resolved entity slugs (the digest path).
TRIGGER_TERMS: dict[str, list[str]] = {
    "arc": ["arc", "arc chain", "arc mainnet", "arc testnet", "arc-compatible", "circle's arc"],
    # Specific multi-word triggers only — bare "base" is a common English word and would inject the
    # fact on every "user base"/"base layer". The entity-resolved slug path covers the normal case.
    "base": ["base chain", "base mainnet", "base network", "beryl upgrade", "b20 token"],
    # Anthropic AI model names — trigger on specific version strings to avoid bare-word false positives.
    "fable": ["fable 5", "claude fable", "fable model"],
    "mythos-ai": ["mythos 5", "mythos v5", "claude mythos", "mythos model"],
    # Cover the fragmented entity names this chain gets extracted under (robinhood-chain,
    # robinhood-app-chain, robinhood-crypto-chain are the same real network) so the fact fires
    # regardless of which literal phrase a given article/bullet used.
    "robinhood-chain": [
        "robinhood chain", "robinhood-chain", "robinhood app chain", "robinhood crypto chain",
        "rh chain",
    ],
}

# Entities that are DEFINITIVELY live on mainnet. The pre-launch auto-discovery in
# ``audit.prelaunch_entities`` keys off the bare word "testnet" (and similar) in an entity's stored
# ``entity_memory.summary`` to catch the next Arc — but that mis-fires when an ESTABLISHED mainnet
# chain/protocol ships an UPGRADE, BRIDGE, or FEATURE to *some* testnet and the summary picks up the
# word (e.g. Base: "Sepolia testnet supports Beryl upgrade…"; Pendle: "contract live on Monad
# testnet"). Listing a slug here tells the heuristic "the entity ITSELF is live — a testnet mention
# is about a sub-feature, not the entity's launch status", so the whole entity is never auto-flagged
# pre-launch. This does NOT relax modality for a genuinely-upcoming sub-feature (a testnet upgrade is
# still spoken as upcoming) — it only stops the whole entity being treated as unreleased. An entity
# must not be in both this set and ``KNOWN_FACTS`` (that set *is* the curated pre-launch list).
ESTABLISHED_MAINNET: set[str] = {
    # Confirmed false positives that motivated this (2026-06-20).
    "base", "pendle",
    # Live mainnet since 2026-07-01; its own pre-launch coverage keeps leaking in (2026-07-20).
    "robinhood-chain",
    # Obvious live L1s / L2s that routinely ship testnet upgrades — guard against recurrence.
    "ethereum", "arbitrum", "optimism", "polygon", "solana", "avalanche", "bnb-chain",
    "tron", "celo", "starknet", "zksync", "scroll", "linea", "mantle", "blast",
    # Major live DeFi protocols frequently mentioned alongside a testnet deployment.
    "aave", "uniswap", "morpho", "aerodrome", "maple", "lido", "curve",
    # Live infra/issuers whose OWN summary mentions a testnet sub-feature (e.g. Circle's
    # summary references Arc's public testnet) — that must not flag the whole entity as
    # pre-launch. Circle (USDC issuer, CCTP live since 2023) is definitively live.
    "circle",
}

# Entity-SELF-reference terms only (never a sub-feature/upgrade name) for the entities we've
# hand-verified need a positive "this is live" correction (currently the KNOWN_FACTS ∩
# ESTABLISHED_MAINNET intersection: base, robinhood-chain). Used by
# ``audit.reverse_modality_violations`` — the mirror of the Arc-direction check, for the
# Robinhood Chain-direction error (an already-live entity still described as future/planned).
# Deliberately NOT the same list as TRIGGER_TERMS: Base's TRIGGER_TERMS intentionally includes
# its own sub-feature names ("beryl upgrade", "b20 token") so the KNOWN_FACTS correction fires
# when someone writes about them — but those SAME terms legitimately carry future-tense language
# ("the Beryl upgrade, expected in Q3") and must NOT be flagged as the chain itself being
# pre-launch. Keep this list to terms that name the entity, never a feature of it.
ESTABLISHED_MAINNET_SELF_TERMS: dict[str, list[str]] = {
    "base": ["base chain", "base mainnet", "base network", "base l2", "base layer 2"],
    "robinhood-chain": [
        "robinhood chain", "robinhood-chain", "robinhood app chain", "robinhood crypto chain",
        "rh chain",
    ],
}

_LABEL = (
    "AUTHORITATIVE KNOWN FACTS (human-verified ground truth — HIGHEST priority, overrides the "
    "source's framing and any prior analyst note; apply a fact ONLY to the entity it names):"
)


def facts_for_slugs(slugs) -> list[str]:
    """Authoritative fact lines for any of the given (already-resolved) entity slugs."""
    out: list[str] = []
    seen: set[str] = set()
    for slug in slugs or []:
        fact = KNOWN_FACTS.get((slug or "").lower())
        if fact and slug not in seen:
            out.append(fact)
            seen.add(slug)
    return out


def facts_for_text(text: str) -> list[str]:
    """Authoritative fact lines whose trigger term appears (word-boundary) in ``text``.

    Used on raw digest input where entities aren't pre-resolved. The longest trigger is enough —
    one hit per entity. A stray English word ("narrative arc") may match, but the fact is
    explicitly scoped ("Circle's Arc…") so an off-topic match is harmless background.
    """
    if not text:
        return []
    low = text.lower()
    out: list[str] = []
    for key, terms in TRIGGER_TERMS.items():
        if any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", low) for t in terms):
            out.append(KNOWN_FACTS[key])
    return out


def block(facts: list[str]) -> str:
    """Render fact lines as a labeled prompt block, or '' when there are none."""
    if not facts:
        return ""
    return _LABEL + "\n" + "\n".join(f" - {f}" for f in facts)
