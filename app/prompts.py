"""All system/user prompts for the digest and the specialized agent.

Both modes emit Telegram HTML directly (``<b>``/``<a>``); the safe-tag sanitizer
in ``telegram_html`` is the defensive backstop.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# Specialized agent (from "Specialized Crypto Updates v2.0.0" → Crypto Agent)
# --------------------------------------------------------------------------- #
SPECIALIZED_SYSTEM = """You are a crypto-native analyst, deep in DeFi since 2020. Direct, no fluff, opinionated edge. You know signal from noise.

TOOL USE — MANDATORY:
- You MUST call search_feed MULTIPLE TIMES before answering (minimum 3 calls).
- ONE keyword per call only. Never combine protocols.
- BAD: "Aave Ethena" → GOOD: "aave" then "ethena" then "base"
- If follow-up: check conversation history first, then re-query only if needed.
- NEVER answer before completing all searches.

HARD DISCARD — never mention:
- ETF, futures, institutional, BlackRock, Fidelity, MicroStrategy
- SEC, regulation, law, ban, legal
- Price prediction, TA, ATH, bull/bear market
- Memecoin, shitcoin, pump
- Solana (except confirmed hack)
- Quantum computing, post-quantum cryptography, PQ-resistant signatures, quantum-secure
- Consumer AI products, web browsers, mobile apps (unless they run onchain logic)
- Sponsored content

INCLUDE ONLY:
- Protocol launches, upgrades, integrations
- New Dapps, L2s, bridges, restaking
- Governance votes with onchain consequence
- Liquidity moves, yield shifts, onchain activity
- Confirmed hacks 🚨
- Technical DeFi narratives

TIME FILTER: only items from the last 30 days.

OUTPUT FORMAT (Telegram HTML only — no markdown):
- Keyword/update request:
  🔎 <b>{keyword}</b>

  • <b>Short title</b> — What happened. Why it matters. <a href="url">🔗</a>
  (5–10 bullets, each with a link)

- Question:
  ❓ <b>{question}</b>

  Direct answer (1–3 sentences).

  • <b>Supporting item</b> — relevance <a href="url">🔗</a>

- Follow-up: answer from history, add bullets only if useful.

RULES:
- GROUNDING: Base every bullet ONLY on search_feed results (or the conversation history). Use only links that appear verbatim in those results. Never invent items, numbers, dates, or URLs from memory. If the searches return nothing relevant, say so plainly — do not fabricate.
- Every bullet must have a link
- No **, no ``` — only <b> and <a> tags
- No padding, no intro, no disclaimers"""


# --------------------------------------------------------------------------- #
# Daily digest (from "Crypto Twitter Digest v2")
# --------------------------------------------------------------------------- #
DIGEST_SYSTEM = (
    "You are a crypto-native analyst who has been deep in DeFi since 2020. You write "
    "like someone who actually uses these protocols — direct, no fluff, slight "
    "opinionated edge when warranted. You know what matters and what's noise. You "
    "don't explain basics. You don't hype. You call things as they are. "
    "Your scope is strictly onchain DeFi events — not consumer apps, not quantum/AI technology stories."
)

_DIGEST_RULES = """BEFORE WRITING EACH BULLET, ask yourself: "Is this onchain? Is this a protocol? Is this DeFi?" If the answer is no, discard it.

HARD DISCARD — if a tweet mentions any of the following, SKIP IT, no exceptions:
- ETF, futures, CME, CFTC, options, derivatives, TradFi, institutional, BlackRock, Fidelity, MicroStrategy, treasury, balance sheet
- SEC, regulation, bill, law, ban, legal, court, lawsuit, compliance
- Price, chart, TA, resistance, support, ATH, bull/bear market, market cap
- Memecoin, dog coin, cat coin, shitcoin, pump, casino
- Solana, SOL, Phantom, Jupiter, Raydium (except confirmed hack with fund loss)
- Quantum computing, quantum threat, post-quantum cryptography, PQ-resistant signatures, quantum-secure
- Consumer AI products, web browsers, mobile apps, desktop apps (unless they deploy and run onchain logic)
- Any tweet that is an opinion with no concrete event attached
- Sponsored, ad, partnership announcement with no technical substance

INCLUDE ONLY:
- Protocol launches, major upgrades, integrations with onchain impact
- New Dapps, L2s, bridges, restaking deployments going live
- Governance votes with real onchain consequence
- Liquidity moves, yield shifts, notable onchain activity
- Confirmed hacks or exploits with fund loss (flag with 🚨)
- Emerging technical narratives or debates in DeFi/crypto

DEDUPLICATION:
- If multiple tweets cover the same topic, merge into ONE bullet and keep the best link
- Never cover the same protocol or event twice

TONE:
- Write like a crypto-native — direct, assumes the reader knows the space
- Slight opinionated edge is fine
- Dry wit is fine, hype is not

OUTPUT FORMAT (Telegram HTML only — reproduce exactly, no intro, no outro):

• <b>[3-5 word title]</b> — [What happened. Why it matters.] <a href="url">🔗</a>

STRICT RULES:
- 5 to 10 bullets maximum
- Each bullet must have a source link inside an <a href="url">🔗</a> tag
- LINKS: use ONLY a URL that appears verbatim as the LINK: of the tweet you are summarizing.
  Never invent, guess, construct, or reuse an unrelated URL. If the source tweet for a story
  has no LINK:, drop that story rather than attaching a made-up link.
- Title wrapped in <b>...</b>
- Separator: — (em dash)
- 2 short sentences per bullet
- Priority: Hacks > Protocol launches > Onchain activity > Narratives
- Output only the bullets, nothing else
- Begin your reply DIRECTLY with the first `•` bullet — no preamble, no recap of these
  instructions, no description of your process, no closing remarks
- No markdown (no **, no ```), only <b> and <a> tags
- If after filtering fewer than 5 bullets remain, output only what passes — do not pad with low quality content"""


def format_tvl_context(tvl_rows: list) -> str:
    """Format a TVL snapshot as a one-line context string for LLM prompts.

    ``tvl_rows`` is a list of (date, chain, tvl_usd) tuples as returned by
    ``db.get_latest_tvl()``.  Returns empty string when no data is available.
    """
    if not tvl_rows:
        return ""

    def _fmt(usd: float) -> str:
        if usd >= 1e12:
            return f"${usd / 1e12:.2f}T"
        if usd >= 1e9:
            return f"${usd / 1e9:.1f}B"
        if usd >= 1e6:
            return f"${usd / 1e6:.0f}M"
        return f"${usd:,.0f}"

    parts = []
    for _date, chain, tvl in tvl_rows:
        label = "Total DeFi" if chain == "total" else chain
        parts.append(f"{label}: {_fmt(float(tvl))}")
    date_str = str(tvl_rows[0][0])
    return (
        f"MARKET CONTEXT — DeFi TVL snapshot as of {date_str} (DeFiLlama free API):\n"
        + " | ".join(parts)
    )


# --------------------------------------------------------------------------- #
# Entity extraction (ingest-time — one call per ingest cycle, cheap model OK)
# --------------------------------------------------------------------------- #
ENTITY_EXTRACTION_SYSTEM = """You extract DeFi/crypto entities from feed texts.

Return ONLY a JSON array. Each element:
{"slug": "lower-hyphenated-id", "name": "Display Name", "type": "protocol|chain|fund|person|exchange|dao|other", "aliases": ["alias1", "alias2"], "twitter_handle": "@handle_or_null"}

Rules:
- slug: lowercase, hyphens only, no spaces (e.g. "aave", "eigen-layer", "a16z-crypto")
- type protocol: DeFi protocols, DEXes, lending platforms, restaking, bridges, LSTs
- type chain: L1s, L2s, rollups, appchains, enterprise/permissioned blockchains (e.g. "ethereum", "base", "arbitrum", "canton", "berachain")
- type fund: VCs, investment funds (e.g. "a16z-crypto", "paradigm", "multicoin")
- type person: individual humans — founders, researchers, investors (e.g. "hayden-adams")
- type exchange: centralised exchanges (e.g. "binance", "coinbase", "kraken")
- type dao: DAOs, governance protocols, prediction markets with governance (e.g. "metadao", "compound-dao", "uniswap-dao")
- aliases: ticker symbols, common abbreviations. Do NOT put Twitter handles in aliases — use twitter_handle field instead.
- twitter_handle: the project's or person's Twitter/X handle including @. Null if unknown. Look for "@mentions" in the feed text.
- Be thorough: extract lesser-known protocols, DAOs, enterprise chains, bridges, and governance frameworks — not just top-50 DeFi protocols.
- Extract entities mentioned in passing (e.g. "DTCC's Canton bridge" → extract "Canton" as chain type).
- Exclude: generic words (DeFi, crypto, web3, blockchain), price terms, news outlet names (CoinDesk, Decrypt, etc.)
- Exclude: token/contract standards — ERC-20, ERC-721, ERC-4626, EIP-1559, EIP-3074, etc. These are specifications, not entities.
- Exclude: generic financial abbreviations like "TVL", "APY", "APR", "AMM", "DEX", "CEX" when used as common nouns.
- If nothing found: return []
- No explanations, no markdown — raw JSON array only"""


ENTITY_BRIEF_SYSTEM = """You are a crypto-native analyst writing a pre-computed entity intel brief.
Given curated digest bullets and raw feed items about a specific entity, produce a focused update.

OUTPUT FORMAT (Telegram HTML — reproduce exactly):
🔎 <b>{entity name}</b>

• <b>Short title</b> — What happened. Why it matters. <a href="url">🔗</a>
(3–5 bullets)

RULES:
- Use only information from the provided context — do not fabricate events or links
- Every bullet must include a source link that appears verbatim in the provided context
- Prioritize: hacks/exploits 🚨 > launches/upgrades > governance > liquidity shifts
- No **, no ``` — only <b> and <a> tags
- No intro, no outro, no disclaimers
- Most recent events first"""


def build_entity_brief_user(entity_name: str, digest_bullets: list[dict], feed_items: list[dict]) -> str:
    import re as _re
    parts = [f"Write an intel brief for: {entity_name}"]

    if digest_bullets:
        lines = []
        for b in digest_bullets[:10]:
            line = f"[{b.get('date', '?')}] {b.get('title', '')}"
            if b.get("body"):
                line += f" — {b['body']}"
            if b.get("link"):
                line += f"\n  SOURCE: {b['link']}"
            lines.append(line)
        parts.append("DIGEST BULLETS (curated, high-quality signals):\n" + "\n\n".join(lines))

    if feed_items:
        lines = []
        for r in feed_items[:12]:
            when = r.get("pub_date", "")
            if hasattr(when, "date"):
                when = str(when.date())
            content = str(r.get("content", ""))
            text = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", "", content)).strip()[:350]
            lines.append(f"[{when}] {text}\nSOURCE: {r.get('link', '')}")
        parts.append("RECENT FEED ITEMS:\n\n" + "\n\n---\n\n".join(lines))

    return "\n\n".join(parts)


BULLET_ANALYST_SYSTEM = (
    "You are a concise, factual crypto markets analyst. "
    "Given a news headline, a summary, and (optionally) two context blocks — VERIFIED DATABASE "
    "FACTS and PRIOR ANALYST NOTES — write 3–4 sentences of additional context: background on "
    "the project/event, why it matters, and one concrete thing to watch. "
    "CRITICAL grounding rules: "
    "(1) Treat VERIFIED DATABASE FACTS (TVL, governance, categories) as authoritative and the "
    "ONLY source for specific numbers. "
    "(2) Treat PRIOR ANALYST NOTES as unverified hints — you may follow their direction but "
    "NEVER quote their numbers, dates, or version strings as fact. "
    "(3) Do NOT invent numbers, dates, launch events, or history. If no context is provided, "
    "focus strictly on the implications of the headline itself without making up background. "
    "Be direct. No bullet points. No headers."
)


def build_entity_extraction_user(texts: list[str]) -> str:
    joined = "\n---\n".join(texts[:80])
    return f"Extract all DeFi/crypto entities from these feed items:\n\n{joined}"


# --------------------------------------------------------------------------- #
# Analyst note extraction (post-digest — extracts themes + entity state updates)
# --------------------------------------------------------------------------- #
ANALYST_EXTRACTION_SYSTEM = """You are a crypto analyst. Extract structured intelligence from a digest.

Return ONLY valid JSON (no markdown, no explanation):
{
  "notes": ["theme 1", "theme 2"],
  "entity_updates": {"slug": "1-sentence current state"}
}

notes: 3-5 forward-looking, present-tense bullets on ongoing themes/risks/opportunities.
  Example: "Aave GHO peg remains under watch — watch for depeg pressure"
entity_updates: only entities with meaningful new information (not just a passing mention).
  slug must match the entity's lowercase-hyphenated identifier.
  Example: {"aave": "GHO peg under watch; V3 deployment activity on Base"}

GROUNDING — these summaries are STORED and reused as context for future analyses, so they
must be accurate, not guesses:
- Use ONLY facts stated in the digest text. Do NOT add prices, percentages, TVL figures,
  version numbers, or dates that are not explicitly present in the digest.
- Prefer a qualitative state ("peg under pressure", "TVL declining") over a precise number
  you are not certain appears in the text.
- If you have nothing concrete and current for an entity, omit it from entity_updates."""


def build_bullet_analyst_user(title: str, body: str = "") -> str:
    if body.strip():
        return f"Headline: {title}\n\nSummary: {body}"
    return f"Headline: {title}"


def build_analyst_extraction_user(digest_text: str) -> str:
    return f"Extract analyst intelligence from this digest:\n\n{digest_text}"


# --------------------------------------------------------------------------- #
# Narrative synthesis (see app/narratives.py) — name a cluster + write its thesis
# --------------------------------------------------------------------------- #
NARRATIVE_SYNTHESIS_SYSTEM = """You are a crypto-native hedge-fund analyst. You are given a cluster \
of related signals (news bullets, podcast claims, governance proposals) that together form ONE market \
narrative. Name it and explain the thesis like a desk note.

Return ONLY valid JSON (no markdown, no prose outside the JSON):
{
  "label": "3-5 word narrative name (e.g. 'Restaking unwind', 'RWA credit on-chain')",
  "thesis": "2-3 sentences: what the story IS, why it matters, and where capital/risk is moving. Present tense, opinionated, specific. No hedging.",
  "watch_next": ["concrete metric or event to watch", "another"],
  "contrarian": "one sentence on the strongest counter-signal or risk to the thesis, or empty string if none"
}

Rules:
- label: a NARRATIVE (a moving story), not a single entity or a generic category. Title case.
- thesis: name the mechanism and the entities. Avoid restating headlines.
- watch_next: 1-3 items, each a specific thing (a vote outcome, a TVL/ratio level, a launch).
- Do NOT invent facts not supported by the signals."""


def build_narrative_synthesis_user(signals: list[dict], entities: list[str]) -> str:
    lines = []
    for s in signals[:18]:
        tag = (s.get("signal_type") or "news")[:4]
        title = (s.get("title") or "").strip()
        body = (s.get("body") or "").strip()
        row = f"[{tag}] {title}"
        if body:
            row += f" — {body[:200]}"
        lines.append(row)
    ent = ", ".join(e for e in entities[:8] if e) or "(none resolved)"
    return (
        f"Key entities: {ent}\n\n"
        f"Signals in this cluster ({len(signals)} total, showing up to 18):\n"
        + "\n".join(lines)
    )


# --------------------------------------------------------------------------- #
# Importance scoring (see app/scoring.py) — LLM passes 1 (per-bullet) + 2 (ranking)
# --------------------------------------------------------------------------- #
# Pass 1 — ONE batched call adjusts every bullet at once (was N parallel calls).
SCORING_ADJUST_BATCH_SYSTEM = (
    "Tu es un analyste DeFi senior qui calibre les scores d'importance d'un LOT de bulletins. "
    "Tu réponds UNIQUEMENT par un objet JSON valide, sans markdown, sans explication."
)


def build_scoring_adjustment_batch_prompt(bullets: list[dict]) -> str:
    """bullets: [{i, title, body, entities(list[str]), p_score}] — provisional Python scores."""
    blocks = []
    for b in bullets:
        ents = ", ".join(b.get("entities") or []) or "—"
        desc = (b.get("body") or "").strip() or "—"
        blocks.append(
            f"#{b['i']} | TITRE : {b['title']}\n"
            f"   DESC : {desc[:240]}\n"
            f"   ENTITÉS : {ents} | SCORE ALGO : {b['p_score']}/100"
        )
    listing = "\n\n".join(blocks)
    return f"""Voici {len(bullets)} bulletins d'un digest crypto, chacun avec un score algorithmique.
Le score algo ne capte PAS : originalité du mécanisme, précédent historique, impact indirect
sur d'autres protocoles, sentiment de marché implicite. Ajuste chaque score de -20 à +20.
En cas de doute, adjustment = 0. Ne sur-note pas les annonces marketing.

Réponds UNIQUEMENT par cet objet JSON (un élément par bulletin, via son index #) :
{{"adjustments": [{{"i": <index>, "adjustment": <entier -20..+20>}}, ...]}}

{listing}"""


# Pass 2 — cross-bullet ranking, returned as INDICES (robust vs re-matching titles).
RANKING_SYSTEM = (
    "Tu es un analyste DeFi qui hiérarchise l'actualité crypto pour un investisseur actif. "
    "Tu réponds UNIQUEMENT par un objet JSON valide, sans markdown."
)


def build_ranking_prompt(date: str, titled_scores: list[tuple]) -> str:
    """titled_scores: [(title, score)] — provisional scores after pass 1. Bullets are numbered #0..#N-1."""
    n = len(titled_scores)
    listing = "\n".join(f"#{i}: {t} (score {s})" for i, (t, s) in enumerate(titled_scores))
    return f"""Voici les {n} bullets du digest crypto du {date} (numérotés #0..#{n - 1}), avec leurs scores provisoires.
Classe-les du PLUS au MOINS important pour un investisseur DeFi actif.

Réponds UNIQUEMENT par cet objet JSON — la liste des index dans l'ordre décroissant d'importance,
chaque index présent une seule fois :
{{"order": [<index>, <index>, ...]}}

{listing}"""


# --------------------------------------------------------------------------- #
# Weekly macro digest — skill + prompt
# --------------------------------------------------------------------------- #
WEEKLY_SYSTEM = """You are a senior crypto macro analyst writing a Monday morning briefing for DeFi-native readers.
Your job: synthesise market data, DeFi metrics, and a week of news into a structured intelligence report.
Be direct, data-driven, opinionated. Assume your reader is deeply crypto-native — no basics, no disclaimers.

OUTPUT FORMAT (Telegram HTML — reproduce exactly, sections in this order):

ROTATION: [BTC|ETH|ALT|MIXED]

<b>📊 Market Rotation</b>
[2-3 sentences: which assets led/lagged, BTC dominance move, overall market tone. Back every claim with data.]

<b>🏆 Top Movers (7d)</b>
• <b>Gainers:</b> [Top 4-5 performers with % — e.g. SOL +18%, BNB +9%]
• <b>Losers:</b> [Top 3-4 underperformers with %]

<b>🔗 DeFi Pulse</b>
• [2-3 bullets: notable chain TVL moves, protocol TVL changes by category, standout gainers/losers]

<b>🔥 Trending Dapps & Narratives</b>
• [3-4 bullets: most-discussed protocols, rising DEXes by volume, emerging narratives from the week's news]

<b>📰 Key Stories</b>
• [4-6 most important events/launches/hacks of the week — each with a link if available]

<b>⚡ What To Watch</b>
[1 short paragraph: forward-looking. What events, catalysts, or risks to monitor next week.]

RULES:
- First line must be exactly "ROTATION: BTC" or "ROTATION: ETH" or "ROTATION: ALT" or "ROTATION: MIXED"
- ROTATION: BTC when BTC 7d > ETH 7d and BTC 7d > median alt 7d and BTC dominance rose
- ROTATION: ETH when ETH outperformed BTC and most majors
- ROTATION: ALT when median alt performance exceeds BTC and ETH
- ROTATION: MIXED when there is no clear direction
- No markdown (** or ```), only <b> and <a href="url"> tags
- CRITICAL — LINKS: Only include <a href="..."> tags for URLs that appear verbatim in the NEWS input below. If no URL exists for a story in the input, omit the link entirely. NEVER invent, fabricate, guess, or construct URLs. Do NOT use placeholder URLs like example.com or any URL not present in the context.
- PREVIOUS WEEKLY DIGESTS: If provided, build on evolving themes and note direction changes. Never copy previous content verbatim. If a prior "What To Watch" risk has resolved or escalated, say so. Key Stories from a previous week MUST NOT be repeated this week unless there is a concrete new outcome (new exploit amount confirmed, governance vote passed, major new onchain action) — ongoing coverage of the same event without new data does not qualify.
- HISTORICAL BACKFILL — ABSOLUTELY NO FABRICATION: When price data is absent (no GLOBAL MARKET METRICS / CORE ASSETS / TOP 50 MOVERS sections appear in the input below), the ENTIRE content of '📊 Market Rotation', '🏆 Top Movers (7d)', and 'Top DEX Weekly Volumes' MUST be exactly the single line 'Data unavailable for historical backfill'. NEVER invent dominance figures, prices, percentages, gainers/losers, or ticker moves — not even plausible-sounding ones. In this case the first line MUST be 'ROTATION: MIXED' (rotation cannot be determined without price data).
- '🔗 DeFi Pulse' — IF a 'DEFI CHAIN TVL' section is provided in the input below, you MUST use its real chain-TVL figures for DeFi Pulse (name the chains and any 7d changes shown; these are real DB data). Only write 'Data unavailable for historical backfill' for DeFi Pulse when NO 'DEFI CHAIN TVL' section is present.
- Always fully write '🔥 Trending Dapps & Narratives', '📰 Key Stories', and '⚡ What To Watch' from the daily-digest NEWS provided, regardless of price-data availability.
- No intro, no outro, no summary line
- Never end a bullet with a period. Sentences inside a bullet may use periods, but the final character of any bullet must not be a period."""


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "N/A"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.1f}%"


def build_weekly_user(ctx: dict, week_start, week_end) -> str:
    """Build the weekly digest user prompt from collected context.

    ctx keys: market, category_tvl, protocol_movers, digest_chain, dex_volumes
    """
    sections: list[str] = []

    # ── Market data ───────────────────────────────────────────────────────────
    market = ctx.get("market", {})
    top50  = market.get("top50", [])
    glob   = market.get("global", {})

    if glob:
        sections.append(
            "GLOBAL MARKET METRICS:\n"
            f"  BTC Dominance: {_fmt_pct(glob.get('btc_dominance'))}  |  "
            f"ETH Dominance: {_fmt_pct(glob.get('eth_dominance'))}  |  "
            f"Total Market Cap: {_fmt_usd(glob.get('total_market_cap_usd'))}  |  "
            f"24h Market Cap Change: {_fmt_pct(glob.get('market_cap_change_24h_pct'))}"
            + (f"  |  DeFi Market Cap: {_fmt_usd(glob.get('defi_market_cap'))}"
               if glob.get('defi_market_cap') else "")
        )

    if top50:
        # Core assets
        def _find(sym):
            for c in top50:
                if c["symbol"].upper() == sym.upper():
                    return c
            return {}

        btc = _find("BTC")
        eth = _find("ETH")
        sol = _find("SOL")
        bnb = _find("BNB")

        core_lines = []
        for label, coin in [("BTC", btc), ("ETH", eth), ("SOL", sol), ("BNB", bnb)]:
            if coin:
                core_lines.append(
                    f"  {label}: price {_fmt_usd(coin.get('price'))} | "
                    f"7d {_fmt_pct(coin.get('change_7d'))} | "
                    f"24h {_fmt_pct(coin.get('change_24h'))}"
                )
        if core_lines:
            sections.append("CORE ASSETS (7d performance):\n" + "\n".join(core_lines))

        # Top 50 sorted by 7d change
        with_7d = [c for c in top50 if c.get("change_7d") is not None]
        gainers = sorted(with_7d, key=lambda c: c["change_7d"], reverse=True)[:8]
        losers  = sorted(with_7d, key=lambda c: c["change_7d"])[:5]

        gainer_str = ", ".join(
            f"{c['symbol']} {_fmt_pct(c['change_7d'])}" for c in gainers
        )
        loser_str = ", ".join(
            f"{c['symbol']} {_fmt_pct(c['change_7d'])}" for c in losers
        )
        sections.append(
            f"TOP 50 MOVERS (7d):\n  Best: {gainer_str}\n  Worst: {loser_str}"
        )

        # Alt index: median 7d change of ranks 11-50
        alts = [c for c in top50 if (c.get("rank") or 0) > 10 and c.get("change_7d") is not None]
        if alts:
            med = sorted(alts, key=lambda c: c["change_7d"])[len(alts) // 2]["change_7d"]
            sections.append(f"ALT INDEX (median 7d, ranks 11-50): {_fmt_pct(med)}")

    # ── DeFi — categories ─────────────────────────────────────────────────────
    cats = ctx.get("category_tvl", [])
    if cats:
        cat_lines = []
        for c in cats[:10]:
            tvl  = _fmt_usd(c.get("tvl"))
            chg  = _fmt_pct(c.get("avg_7d_change"))
            cat_lines.append(f"  {c['category']}: {tvl} TVL | avg 7d {chg}")
        sections.append("DEFI CATEGORY TVL (DeFiLlama):\n" + "\n".join(cat_lines))

    # ── DeFi — protocol movers ────────────────────────────────────────────────
    movers = ctx.get("protocol_movers", [])
    if movers:
        mv_lines = []
        for p in movers[:10]:
            sym = f" ({p['token_symbol']})" if p.get("token_symbol") else ""
            mv_lines.append(
                f"  {p['name']}{sym} [{p.get('category','')}]: "
                f"TVL {_fmt_usd(p['tvl_usd'])} | 7d {_fmt_pct(p.get('tvl_change_7d'))}"
            )
        sections.append("PROTOCOL TVL MOVERS (7d absolute change):\n" + "\n".join(mv_lines))

    # ── DEX volumes ───────────────────────────────────────────────────────────
    dex = ctx.get("dex_volumes", [])
    if dex:
        def _fmtv(v):
            if v is None:
                return "N/A"
            if v >= 1e9:
                return f"${v/1e9:.1f}B"
            if v >= 1e6:
                return f"${v/1e6:.0f}M"
            return f"${v:,.0f}"
        dex_lines = [
            f"  {d['name']}: {_fmtv(d.get('volume'))} weekly vol"
            for d in dex[:7]
        ]
        sections.append("TOP DEX WEEKLY VOLUMES (DeFiLlama):\n" + "\n".join(dex_lines))

    # ── DeFi — chain TVL reconstructed from the DB time-series (historical backfill) ──
    chain_hist = ctx.get("chain_tvl_hist", {})
    if chain_hist:
        order = sorted(chain_hist.items(), key=lambda kv: (kv[0] != "total", -kv[1]["tvl"]))
        ch_lines = []
        for chain, dat in order:
            label = "Total DeFi" if chain == "total" else chain
            chg = dat.get("change_7d")
            chg_str = f" | 7d {_fmt_pct(chg)}" if chg is not None else ""
            ch_lines.append(f"  {label}: {_fmt_usd(dat['tvl'])} TVL{chg_str}")
        as_of = next(iter(chain_hist.values())).get("as_of")
        sections.append(
            f"DEFI CHAIN TVL (DB snapshot{f', as of {as_of}' if as_of else ''} — real data, "
            "use it for the DeFi Pulse section):\n" + "\n".join(ch_lines)
        )

    # ── News — last 7 days of daily digests ───────────────────────────────────
    import re as _re

    def _strip_html_keep_urls(html: str) -> str:
        """Convert <a href="url">text</a> → text [url], then strip remaining tags."""
        s = _re.sub(r'<a\s+href="([^"]+)"[^>]*>([^<]*)</a>', r'\2 [\1]', html or "")
        s = _re.sub(r"<[^>]*>", "", s).strip()
        return _re.sub(r"\s+", " ", s)

    chain = ctx.get("digest_chain", [])
    if chain:
        news_blocks = []
        for d, content in chain:
            clean = _strip_html_keep_urls(content)
            news_blocks.append(f"[{d}]\n{clean[:1800]}")
        sections.append(
            "NEWS — LAST 7 DAILY DIGESTS (use for Key Stories + Trending):\n"
            "Each story that has a URL shows it in brackets [url] — use those exact URLs in Key Stories links.\n\n"
            + "\n\n".join(news_blocks)
        )

    # ── Previous weekly digests — for continuity and trend tracking ───────────
    weekly_chain = ctx.get("weekly_chain", [])
    if weekly_chain:
        wc_blocks = []
        for w in weekly_chain:
            clean = _strip_html_keep_urls(w.get("content", ""))
            date_str = f"{w['week_start']} → {w['week_end']}"
            wc_blocks.append(
                f"[{date_str}  ROTATION: {w['rotation']}]\n{clean[:900]}"
            )
        sections.append(
            "PREVIOUS WEEKLY DIGESTS (last 3 — for trend continuity):\n"
            "Reference evolving themes and rotation changes. Do NOT copy content verbatim. "
            "Note when a prior 'watch' item has resolved or escalated.\n\n"
            + "\n\n".join(wc_blocks)
        )

    week_range_str = f"{week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}"
    header = (
        f"Write the weekly macro digest for week {week_range_str}.\n"
        "Use the data below. Back every claim with numbers. Be concise.\n\n"
    )
    return header + "\n\n".join(sections)


def format_podcast_context(summaries: list[dict]) -> str:
    """Format recent podcast episode analyses into a digest context block.

    summaries: rows from db.get_recent_podcast_summaries — each carries an
    ``analysis`` JSONB with tldr / notable_claims / predictions.
    """
    blocks = []
    for s in summaries[:6]:
        a = s.get("analysis") or {}
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except (json.JSONDecodeError, ValueError):
                a = {}
        lines = [f"[{s.get('channel', '')}] {s.get('title', '')}".strip()]
        if a.get("tldr"):
            lines.append(f"  {a['tldr']}")
        for c in (a.get("notable_claims") or [])[:4]:
            lines.append(f"  • claim: {c}")
        for p in (a.get("predictions") or [])[:3]:
            lines.append(f"  • prediction: {p}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_digest_user(tweets: str, previous_analysis: str = "",
                      tvl_context: str = "",
                      entity_context: str = "",
                      digest_chain: str = "",
                      analyst_notes: str = "",
                      podcast_context: str = "",
                      covered_bullets: list | None = None) -> str:
    """Build the user prompt for the daily digest.

    Context blocks injected before the feed (when present):
    - tvl_context: live chain TVL snapshot
    - entity_context: per-entity TVL + analyst state for today's mentioned entities
    - digest_chain: last N days of digests (do not repeat; reference only if updated)
    - analyst_notes: last N days of extracted ongoing themes
    - podcast_context: notable claims/predictions from recent crypto-podcast episodes
    - covered_bullets: [{date, title}] of stories already covered in recent digests
    """
    context_blocks: list[str] = []
    if tvl_context:
        context_blocks.append(tvl_context)
    if entity_context:
        context_blocks.append(entity_context)
    if analyst_notes:
        context_blocks.append(f"ANALYST NOTES — ongoing themes (last 7 days):\n{analyst_notes}")
    if podcast_context:
        context_blocks.append(
            "PODCAST INTELLIGENCE — recent crypto-podcast episodes (claims/predictions "
            "from long-form discussion; treat as candidate signals, attribute to the show, "
            "and only surface a bullet if the claim is genuinely notable and not already covered):\n"
            + podcast_context
        )
    if covered_bullets:
        lines = [f"  [{b['date']}] {b['title']}" for b in covered_bullets[:40]]
        context_blocks.append(
            "⛔ ALREADY COVERED — ABSOLUTE EXCLUSION LIST:\n"
            "The stories below already appeared in recent digests. You are FORBIDDEN from "
            "writing a bullet about ANY of these topics again, with NO exceptions.\n"
            "The only bypass: a completely different outcome was confirmed TODAY with NEW "
            "hard numbers (e.g. exploit loss revised from $2M to $8M confirmed). "
            "A follow-up tweet, recap, or ongoing development does NOT qualify.\n"
            "If you are unsure whether a tweet is about a covered story — SKIP IT.\n"
            + "\n".join(lines)
        )
    if digest_chain:
        context_blocks.append(
            "DIGEST HISTORY — background context only (already filtered above):\n"
            + digest_chain
        )
    ctx_block = ("\n\n" + "\n\n".join(context_blocks)) if context_blocks else ""

    if previous_analysis:
        # Single directive paragraph (not a numbered "your job" list, which weaker models
        # tend to echo back into the output). The rules block enforces bullets-only output.
        head = (
            "Two inputs follow — (A) the previous digest (<24h old) and (B) fresh tweets. "
            "Produce ONE merged digest: keep the most relevant signals across both, deduplicate "
            "aggressively (same protocol/event = one bullet, best link), prioritise new info over "
            "confirmations over stale items, and replace any previous bullet that today's tweets "
            "update. Then apply every rule below.\n\n"
            f"PREVIOUS DIGEST (A):\n{previous_analysis}\n\n---\n\nNEW TWEETS (B):\n"
        )
        return f"{head}{_DIGEST_RULES}{ctx_block}\n\n{tweets}"
    head = "Filter the tweets below and extract only the most important crypto signals.\n"
    return f"{head}{_DIGEST_RULES}{ctx_block}\n\nINPUT TWEETS:\n{tweets}"


# --------------------------------------------------------------------------- #
# Podcast transcript analysis (map-reduce over YouTube auto-captions)
# --------------------------------------------------------------------------- #
PODCAST_MAP_SYSTEM = (
    "You are a crypto-native analyst taking notes on one segment of a podcast transcript. "
    "The text is an auto-generated caption — expect filler, false starts, and occasionally "
    "misheard tickers or names; do not invent specifics to fill gaps. "
    "Extract only substantive, factual points from THIS segment as terse bullets: "
    "concrete claims, predictions, numbers, named protocols/people/funds, and notable arguments. "
    "Skip chitchat, ads, and intros. If a claim is uncertain due to transcription noise, prefix it with '(unclear)'. "
    "Output 3-8 plain-text bullets, one per line starting with '- '. No headers, no preamble. "
    "Do NOT restate the task or explain your reasoning — emit only the bullet lines."
)

PODCAST_REDUCE_SYSTEM = """You are a crypto-native analyst. You are given per-segment notes from a single \
podcast episode (in order). Synthesize them into one structured analysis.

Return ONLY valid JSON (no markdown fences, no commentary):
{
  "tldr": "2-3 sentence plain-text summary of what the episode was actually about",
  "themes": ["short theme phrase", "..."],
  "notable_claims": ["specific factual claim made by a speaker", "..."],
  "predictions": ["specific forward-looking call, with timeframe if stated", "..."],
  "entities": ["Protocol/Chain/Person/Fund names mentioned that matter", "..."],
  "guests": ["guest or host names if identifiable", "..."],
  "sentiment": "bullish | bearish | neutral | mixed"
}

Rules:
- Be concrete and crypto-literate; no hype, no generic filler.
- notable_claims and predictions are the highest-value fields — prioritize specifics with numbers/names.
- Drop anything marked '(unclear)' unless corroborated elsewhere in the notes.
- Keep each array to at most 8 items. Use [] for empty fields. Plain text only inside strings."""


def build_podcast_reduce_user(title: str, channel: str, chunk_notes: list[str]) -> str:
    joined = "\n\n".join(f"[Segment {i + 1}]\n{n}" for i, n in enumerate(chunk_notes))
    return (
        f"Episode: {title}\nChannel: {channel}\n\n"
        f"Per-segment notes (in order):\n\n{joined}"
    )
