"""Render a daily digest into spoken audio briefings in THREE lengths.

One set of grounded signals → three shows per digest date (``app/config.py`` ``BRIEFING_VARIANTS``):
``short`` (~90-sec single-host flash), ``standard`` (~6-min two-voice podcast, the default), and
``explainer`` (~12-min two-voice deep dive). An LLM rewrites the day's TOP grounded bullet analyses
(from ``digest_bullet_analysis``) into a spoken script, which a free TTS voice (``app/tts.py``)
renders to audio. Each variant is stored as a ``(digest_date, variant)`` row in ``digest_audio``;
the web player switches between them and Telegram sends the ``standard`` one.

Crucially, the three lengths share ONE pipeline and ONE set of enriched signals — same
``known_facts`` / temporal-modality rails (``prompts._BRIEFING_GROUNDING``) and the same pre-synth
modality gate — so a longer or shorter show adds NO hallucination surface; only verbosity differs.

Reuse, don't re-derive (mirrors ``app/threads.py``):
  * Bullets + scores come from ``threads._ordered_bullets`` — already grounded + importance-scored
    + ordered, the single source of truth shared with the Twitter thread.
  * The SCRIPT is a pure REWRITE under a strict no-new-facts rule, with the same ``known_facts`` /
    temporal-modality rails as the thread, and the SAME modality safety gate before audio is cut.
  * TTS is a swappable commodity behind ``tts.synthesize()``.

Resilience (mirrors the rest of the pipeline): best-effort, never breaks the digest. Script LLM
failure → no audio (don't synth garbage). Modality-gate trip → status 'blocked', nothing sent (the
audio is outward-facing + irreversible, so it fails CLOSED). TTS failure → status 'failed'. The
orchestrator wraps the whole thing in ``run_step`` so an exception never reaches the digest.
"""
from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import date as date_t, datetime, timedelta, timezone

from . import config, db, llm, prompts, tts, util

log = logging.getLogger(__name__)

# Reasoning-leak guard, take 2 (2026-07-03 incident): the <think>-tag strip (llm.strip_think,
# shared with threads/podcasts/entity_brief/digest) only catches a
# leak the model wraps in its own delimiter. A reasoning model can instead narrate its planning
# INLINE under the same 'HOST:'/'EXPERT:' labels real dialogue uses — e.g. "HOST: We need to
# produce a script with at least 1035 words... Must include cold open..." — which is syntactically
# identical to a real turn, so `_SPEAKER_RE`-based parsing alone can't tell them apart; it has to be
# caught by CONTENT. Mirrors the phrase-echo detector in podcasts.py/entity_brief.py (`_META_RE`),
# tuned to THIS prompt's own vocabulary (see prompts.py `_BRIEFING_*` blocks) — a real host/expert
# line about crypto news never says these. Bare verbs like "must keep"/"must avoid"/"we need to
# ensure" were tried and DROPPED — they collide with genuine analyst hedging ("we must keep an eye
# on regulatory risk"), caught by this module's own test suite. Every phrase below is anchored to
# the specific OBJECT the leak used (the show's own word-count/structure/format), not a bare verb,
# which is what keeps collision risk with real commentary near zero. Validated against all 162
# historical stored scripts (4,986 turns): 0 false positives, catches all 10 contaminated turns.
_META_RE = re.compile(
    r"\bwe need to (?:produce|write|design|create|reach|hit|expand|cover)\b|"
    r"\bmust not invent\b|\bmust keep (?:the same|all)\b|\bmust alternate\b|"
    r"\bmust vary host\b|\bmust avoid (?:hype|filler)\b|\bmust define jargon\b|"
    r"\bmust not repeat (?:a name|names)\b|"
    r"\bat least \d+ words\b|\b\d+[\-–]\d+\s*words\b|\bword count\b|"
    r"\bchapter marker\b|\bmarker line\b|\bcold open\b|"
    r"\bwe can mention\b|"
    r"\bhost follow-?up questions?\b|\bthe signals? (?:below|given)\b|\bpattern:\s*host\b",
    re.IGNORECASE,
)
# A turn that is NOTHING BUT a stage direction / rehearsal placeholder ('...', '(lead story)') —
# the model rehearsing the turn structure instead of writing it. `_BRIEFING_EAR_RULES` already bans
# parentheticals as spoken content, so a turn that is only one is never legitimate output either way.
_PLACEHOLDER_RE = re.compile(r"^\.{2,}\s*(?:\([^)]{0,80}\))?$|^\([^)]{1,80}\)$")
# A leaked "note to self" tacked onto the END of an otherwise-real turn (e.g. "...flowing next. Now
# marker." / "...depth of the order book. Now move to next story.") — the model reminding itself
# when to insert a chapter marker or transition. Anchored at end-of-turn + a short fixed set of
# alternatives to keep collision risk with genuine dialogue near zero (a host would never end a
# turn on the word "marker").
_STAGE_NOTE_SUFFIX_RE = re.compile(
    r"\s*\bNow\s+(?:marker|next story|move to next story|move on|the next story)\.?\s*$",
    re.IGNORECASE,
)


def _is_leak_turn(text: str) -> bool:
    """A parsed turn that is leaked planning prose or a rehearsal placeholder, not real dialogue —
    see the `_META_RE`/`_PLACEHOLDER_RE` incident note above."""
    t = (text or "").strip()
    return bool(_PLACEHOLDER_RE.match(t) or _META_RE.search(t))


# "Say it aloud" sanitizers — a safety net under the prompt rules. TTS reads markdown, URLs,
# emoji and stray symbols literally, so strip anything the model leaves behind.
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")          # [label](url) → label
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U0000FE0F]")
_DASH_NUM_RE = re.compile(r"(?<=\d)\s*[—–]\s*(?=\d)")        # numeric range → plain hyphen
_DASH_SEP_RE = re.compile(r"\s*[—–]\s*|\s+--+\s+")           # prose dashes → comma pause
_LIST_MARK_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+")      # leading bullet/number markers
# Dialogue turn labels the script LLM emits (see prompts.build_briefing_system). Tolerant of
# bold/quote markers and a few dash variants the model may slip in.
_SPEAKER_RE = re.compile(r"^\s*[*_>\"'\s]*\b(HOST|EXPERT)\b\s*[:\-–—]\s*", re.IGNORECASE)
# Chapter marker the script LLM emits before each story (see prompts.build_briefing_system): a line
# that is just one-to-four hash marks + a short title. Parsed out (never spoken) into navigation
# chapters. Tolerant of stray markdown bold the model may wrap it in.
_CHAPTER_RE = re.compile(r"^\s*#{1,4}\s*[*_]*\s*(.+?)\s*[*_]*\s*$")


# ── Acronym pronunciation ────────────────────────────────────────────────────
# Neural TTS voices mangle crypto jargon: they read "MEV" as the word "mev" and swallow "OP Stack".
# The prompt asks the LLM to expand acronyms, but this is the deterministic safety net for whatever
# slips through. Each term maps to a spoken form — a natural full-word expansion where one reads
# cleanly, or a hyphenated phonetic spelling ("em-ee-vee") the voice says as letters. CASE-SENSITIVE
# and word-boundary matched on purpose, so the token "Dao"/"op" the name isn't touched. Extend as
# new jargon turns up garbled in a briefing.
_LETTER_SOUND = {
    "A": "ay", "B": "bee", "C": "see", "D": "dee", "E": "ee", "F": "eff", "G": "jee",
    "H": "aitch", "I": "eye", "J": "jay", "K": "kay", "L": "el", "M": "em", "N": "en",
    "O": "oh", "P": "pee", "Q": "cue", "R": "ar", "S": "ess", "T": "tee", "U": "you",
    "V": "vee", "W": "double-you", "X": "ex", "Y": "why", "Z": "zee",
}


def _spell(acr: str) -> str:
    """'MEV' -> 'em-ee-vee' — hyphen-joined so the voice reads it as one initialism."""
    return "-".join(_LETTER_SOUND.get(c.upper(), c) for c in acr if c.isalpha())


_SPEAK_AS = {
    # Full-word expansions read more naturally than letters here.
    "OP Stack": "Op Stack", "OP stack": "Op Stack", "OP-Stack": "Op Stack", "OP-stack": "Op Stack",
    "TVL": "total value locked", "RWA": "real-world assets", "RWAs": "real-world assets",
    "ATH": "all-time high", "AUM": "assets under management",
    # Protocol version tags — the prompt asks for 'version four', this catches the 'V4' that slips
    # through (a bare 'V2' reads as "vee-two"). Capitalised only; \b-safe inside other tokens.
    "V1": "version one", "V2": "version two", "V3": "version three", "V4": "version four",
    "V5": "version five",
    # "DeFi" → spoken "DEE-fye" (rhymes with hi-fi). NB: a short non-word like "fy" gets read as the
    # LETTERS F-Y ("eff-why" ≈ "dee F way"), so the second syllable must be a real /faɪ/ word — "fie"
    # — which the voice pronounces correctly. Cover the casings that appear; "defi" inside a word
    # ('definitely') is \b-safe.
    "DeFi": "dee fie", "Defi": "dee fie", "DEFI": "dee fie", "defi": "dee fie",
    "L1": "layer one", "L2": "layer two", "L3": "layer three",
    # Plural forms ("L2s") aren't caught by the singular \b rules (the trailing 's' blocks the
    # boundary) and read as "L-two-S" — handle them explicitly (longest-first sorting wins).
    "L1s": "layer ones", "L2s": "layer twos", "L3s": "layer threes",
    "PoS": "proof of stake", "PoW": "proof of work", "P2P": "peer to peer",
    # Liquid-staking / restaking ETH tokens read as noise ("rsETH"→"rzeth"). Give each a stable
    # spoken form: a plain name where one reads cleanly, else the letters then "eth" ('rs eth').
    "stETH": "staked eth", "wstETH": "wrapped staked eth",
    "rsETH": _spell("rs") + " eth", "weETH": "wee eth",
    "ezETH": _spell("ez") + " eth", "cbETH": _spell("cb") + " eth",
    # Letter-spelled initialisms.
    "MEV": _spell("MEV"), "EIP": _spell("EIP"), "ERC": _spell("ERC"), "EVM": _spell("EVM"),
    "zkEVM": "zee-kay " + _spell("EVM"), "ZK": "zee-kay",
    "NFT": _spell("NFT"), "NFTs": _spell("NFT") + "s", "ETF": _spell("ETF"),
    "ETFs": _spell("ETF") + "s", "AMM": _spell("AMM"), "APR": _spell("APR"),
    "APY": _spell("APY"), "CEX": _spell("CEX"), "SEC": _spell("SEC"), "CFTC": _spell("CFTC"),
    "KYC": _spell("KYC"), "AML": _spell("AML"), "LST": _spell("LST"), "LRT": _spell("LRT"),
    "API": _spell("API"), "APIs": _spell("API") + "s", "SDK": _spell("SDK"),
    "SDKs": _spell("SDK") + "s", "IoT": _spell("IoT"), "SME": _spell("SME"),
    "SMEs": _spell("SME") + "s", "FX": _spell("FX"), "LP": _spell("LP"), "UI": _spell("UI"),
    "PT": _spell("PT"), "SVR": _spell("SVR"),
    # Tickers + org acronyms that garble as words ('SECZ'→"secz", 'NYSE'→"nice"). Spell the letters.
    # ONLY the genuinely unpronounceable ones — DAO/HYPE/SPAC/NASDAQ/DEX/AERO/LINK/ADA read FINE as
    # words and are deliberately left alone (spelling them would make it worse).
    "NYSE": _spell("NYSE"), "DTCC": _spell("DTCC"), "DCG": _spell("DCG"), "SBI": _spell("SBI"),
    "ENS": _spell("ENS"), "BNB": _spell("BNB"), "XRP": _spell("XRP"), "SNX": _spell("SNX"),
    "GNO": _spell("GNO"), "SECZ": _spell("SECZ"), "SPCX": _spell("SPCX"), "QQQ": _spell("QQQ"),
    "NIDR": _spell("NIDR"), "AAVE": "Aave",
    # Stablecoins: USDT has a real spoken name ("Tether") — use it, it reads far better than letters.
    # The rest have no spoken name, so give one stable pronunciation (the prompt's don't-repeat-a-name
    # rule keeps these from being hammered turn after turn). The app-specific / coined ones
    # ('sUSDe', 'pUSD', 'AUSD', 'RLUSD') the prompt would rather we describe in plain words, but when
    # the model leaves the raw token this at least renders it as clean letters, not noise.
    "USDT": "Tether", "USDC": _spell("USDC"), "DAI": "dye", "USDe": "you-ess-dee-ee",
    "USDE": "you-ess-dee-ee", "USDS": _spell("USDS"), "USD": _spell("USD"),
    "AUSD": _spell("AUSD"), "RLUSD": _spell("RLUSD"), "pUSD": _spell("pUSD"),
    "sUSD": _spell("sUSD"), "sUSDe": _spell("sUSDe"), "msUSD": _spell("msUSD"),
    "srUSDe": _spell("srUSDe"),
}
# Longest first so "OP Stack" beats a bare "OP", "NFTs" beats "NFT", etc.
_SPEAK_AS_RE = [
    (re.compile(rf"\b{re.escape(term)}\b"), say)
    for term, say in sorted(_SPEAK_AS.items(), key=lambda kv: -len(kv[0]))
]


def _speak_jargon(text: str) -> str:
    for pattern, say in _SPEAK_AS_RE:
        text = pattern.sub(say, text)
    return text


# ── Deterministic spoken-form safety net ──────────────────────────────────────
# The prompt asks the LLM to write numbers/currency/tickers the way a person says them. This is the
# fallback for whatever slips through — a raw '$2.3B', '46%' or bare 'BTC' is read by neural TTS as
# garbled symbols/letters. Conservative on purpose: only the unambiguous symbol forms and the three
# top tickers (which never collide with common words), so the human-friendly phrasing the LLM
# already produced is left untouched.
_MAGNITUDE = {"b": "billion", "m": "million", "k": "thousand", "t": "trillion"}
# '$2.3 billion' (already-worded magnitude) → '2.3 billion dollars'. Run FIRST so the bare-symbol
# rule below doesn't turn it into '2.3 dollars billion'.
_CURRENCY_WORD_RE = re.compile(
    r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(billion|million|thousand|trillion)\b", re.IGNORECASE)
# '$2.3B' / '$900M' / '$45' → spoken. Suffix optional; word boundary keeps '$45m' vs '$45million'
# both working and stops it eating into a following word.
_CURRENCY_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)([BbMmKkTt])?\b")
_PERCENT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*%")
# Bare top tickers the prompt wants said by name. Case-sensitive + word-boundary: 'BTC' the symbol
# only, never a lowercase word. Kept tiny — most tickers ARE common words and must not be rewritten.
_BARE_TICKER = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana"}
_BARE_TICKER_RE = re.compile(r"\b(" + "|".join(map(re.escape, _BARE_TICKER)) + r")\b")


def _say_numbers(text: str) -> str:
    """Convert leftover symbol-form money/percent to spoken words ('$2.3B' → '2.3 billion dollars',
    '46%' → '46 percent'). Digits are kept (TTS reads '46 percent' cleanly); only the symbols go."""
    def _drop_commas(num: str) -> str:
        return num.replace(",", "")

    text = _CURRENCY_WORD_RE.sub(
        lambda m: f"{_drop_commas(m.group(1))} {m.group(2).lower()} dollars", text)

    def _cur(m):
        num = _drop_commas(m.group(1))
        mag = _MAGNITUDE.get((m.group(2) or "").lower())
        return f"{num} {mag} dollars" if mag else f"{num} dollars"

    text = _CURRENCY_RE.sub(_cur, text)
    text = _PERCENT_RE.sub(r"\1 percent", text)
    return text


def _say_tickers(text: str) -> str:
    """Last-resort: a bare top ticker the LLM left as letters → its spoken name."""
    return _BARE_TICKER_RE.sub(lambda m: _BARE_TICKER[m.group(1)], text)


# 'Layer 2' / 'Layer-2' / 'Layer 2s' written with the DIGIT (vs the 'L2' token handled in _SPEAK_AS)
# — neural voices say "Layer two-S" for the plural. Spell the number so it reads "Layer twos". The
# separator class allows the unicode hyphens the model loves (handled by _UNICODE_HYPHENS too).
_LAYER_WORD = {"1": "one", "2": "two", "3": "three"}
_LAYER_RE = re.compile(r"\bLayer[ \-‐‑]([123])(s?)\b", re.IGNORECASE)
# Non-breaking / unicode HYPHENS the model sprinkles through compound words ('fourth‑generation',
# 'cross‑chain'). They are word-joiners, not pauses, so collapse them to a plain hyphen BEFORE the
# em/en-dash → comma rule runs (which must NOT swallow them).
_UNICODE_HYPHENS = dict.fromkeys(map(ord, "‐‑‒"), "-")
# Spoken-form odds and ends the voices garble.
_TWENTYFOUR_SEVEN_RE = re.compile(r"\b24/7\b")
_XYZ_RE = re.compile(r"\bxyz\b", re.IGNORECASE)   # 'Horyon dot xyz' → letters, not "ziz"


def _say_layers(text: str) -> str:
    return _LAYER_RE.sub(
        lambda m: f"Layer {_LAYER_WORD[m.group(1)]}{'s' if m.group(2) else ''}", text)


def _say_misc(text: str) -> str:
    text = _TWENTYFOUR_SEVEN_RE.sub("twenty-four seven", text)
    return _XYZ_RE.sub("X Y Z", text)


def _normalize_for_speech(text: str) -> str:
    """Strip everything a TTS voice would mispronounce (markdown, URLs, emoji, dashes, list
    markers). The LLM does the ticker/number expansion; this only cleans up what slips through."""
    text = _MD_LINK_RE.sub(r"\1", text or "")
    text = _STAGE_NOTE_SUFFIX_RE.sub("", text)  # leaked "Now marker."-style note tacked on the end
    text = _URL_RE.sub("", text)
    text = (text or "").translate(_UNICODE_HYPHENS)  # NB-hyphens → '-' before the dash→comma rule
    text = re.sub(r"[*_`#>]+", "", text)        # markdown emphasis / headers / quotes / code ticks
    text = _EMOJI_RE.sub("", text)
    text = _speak_jargon(text)                  # expand crypto acronyms TTS voices garble
    text = _say_layers(text)                    # 'Layer 2s' → 'Layer twos' (digit form)
    text = _say_misc(text)                      # '24/7' → 'twenty-four seven', 'xyz' → 'X Y Z'
    text = _say_numbers(text)                   # '$2.3B'/'46%' → spoken words (LLM fallback)
    text = _say_tickers(text)                   # bare BTC/ETH/SOL → Bitcoin/Ethereum/Solana
    text = _DASH_NUM_RE.sub("-", text)
    text = _DASH_SEP_RE.sub(", ", text)
    lines = [_LIST_MARK_RE.sub("", ln) for ln in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _date_label(d: date_t) -> str:
    """'Wednesday, June 18, 2026' with no zero-padded day."""
    return d.strftime("%A, %B %d, %Y").replace(" 0", " ")


def _parse_turns(script: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    """Split a labeled dialogue script into turns + chapter titles. Returns
    ``([(speaker, text, chapter_idx)], chapter_titles)`` where ``speaker`` is 'HOST'/'EXPERT' and
    ``chapter_idx`` indexes ``chapter_titles`` (index 0 = the intro / cold open). ``## Title`` lines
    open a new chapter and are NOT spoken. Continuation lines attach to the open turn; any preamble
    before the first speaker label becomes a HOST turn so nothing is dropped. Text is NOT yet
    speech-normalized."""
    turns: list[list] = []
    titles: list[str] = ["Intro"]
    cur = 0
    for raw in (script or "").splitlines():
        # A speaker label always wins over the chapter regex (e.g. 'HOST: ...' isn't a marker).
        if not _SPEAKER_RE.match(raw):
            cm = _CHAPTER_RE.match(raw)
            if cm and cm.group(1).strip():
                titles.append(cm.group(1).strip())
                cur = len(titles) - 1
                continue
        m = _SPEAKER_RE.match(raw)
        if m:
            turns.append([m.group(1).upper(), raw[m.end():].strip(), cur])
        elif turns:
            turns[-1][1] = (turns[-1][1] + " " + raw.strip()).strip()
        elif raw.strip():
            turns.append(["HOST", raw.strip(), cur])
    return [(sp, txt, ch) for sp, txt, ch in turns if txt], titles


def _speaker_turns(raw_script: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    """Parse + speech-normalize each turn. Returns ``([(speaker, spoken_text, chapter_idx)],
    chapter_titles)``, dropping empties and reasoning-leak/placeholder turns (`_is_leak_turn`)."""
    parsed, titles = _parse_turns(raw_script)
    out = []
    leaked = 0
    for sp, txt, ch in parsed:
        if _is_leak_turn(txt):
            leaked += 1
            continue
        spoken = _normalize_for_speech(txt)
        if spoken:
            out.append((sp, spoken, ch))
    if leaked:
        log.warning("briefing: dropped %d reasoning-leak/placeholder turn(s), %d turn(s) kept",
                    leaked, len(out))
    return out, titles


_ENTITY_TYPE_STRIP_RE = re.compile(r"\s*\([^)]+\)$")


def _build_chapters(turns: list[tuple[str, str, int]], titles: list[str],
                    durations: list[float],
                    signals: "list[dict] | None" = None) -> list[dict]:
    """Map each chapter to its start time (seconds) from per-turn audio durations (index-aligned
    with ``turns``). Returns an ordered ``[{title, start, bullet_title?, entities?}]`` with
    chapters in first-appearance order; [] if there's nothing to navigate.
    When ``signals`` is provided, each story chapter (ch > 0) is enriched with the title and
    entity names from the corresponding signal (ch 1 → signals[0], ch N → signals[N-1])."""
    if not durations or len(durations) != len(turns):
        return []
    starts: dict[int, float] = {}
    clock = 0.0
    for (_, _, ch), dur in zip(turns, durations):
        if ch not in starts:
            starts[ch] = clock
        clock += dur or 0.0
    if len(starts) < 2:  # only the intro → nothing worth navigating
        return []
    out = []
    for ch in sorted(starts):
        title = titles[ch] if ch < len(titles) else f"Part {ch}"
        entry: dict = {"title": title, "start": int(starts[ch])}
        if signals and ch > 0:
            sig_idx = ch - 1
            if sig_idx < len(signals):
                s = signals[sig_idx]
                bt = (s.get("title") or "").strip()
                if bt:
                    entry["bullet_title"] = bt
                raw_ents = [e for e in (s.get("entities") or []) if e]
                if raw_ents:
                    # Strip the "(type)" suffix used for spoken introductions — display names only.
                    names = [_ENTITY_TYPE_STRIP_RE.sub("", e).strip() for e in raw_ents[:4]]
                    names = [n for n in names if n]
                    if names:
                        entry["entities"] = names
        out.append(entry)
    return out


def _format_transcript(turns: list[tuple[str, str, int]]) -> str:
    """Human-readable transcript stored in ``digest_audio.script`` (re-synth / captions / debug).
    Uses the on-air first names so the stored record reads like the podcast."""
    names = {"HOST": config.BRIEFING_HOST_NAME, "EXPERT": config.BRIEFING_EXPERT_NAME}
    return "\n\n".join(f"{names.get(sp, sp.title())}: {txt}" for sp, txt, _ in turns)


# Spoken type labels for entity introductions — keep them the way a host would say them aloud
# ('the lending protocol', 'the blockchain'). 'person'/'other' carry no useful spoken type.
_ENTITY_TYPE_LABEL = {
    "protocol": "protocol", "chain": "blockchain", "dao": "DAO",
    "exchange": "exchange", "fund": "fund",
}


def _entity_descriptors(text: str) -> list[str]:
    """'Name (type)' tags for the entities in a signal, so the expert can introduce a player
    didactically ('Aave, the lending protocol') WITHOUT importing any unverified figures or events
    (name + type only — both safe). Best-effort; returns [] on any failure."""
    try:
        from . import entities  # lazy: heavy module, render-time only

        slugs = entities.detect_entities_in_text(text)
        if not slugs:
            return []
        out = []
        for r in db.get_entities_by_slugs(slugs):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            label = _ENTITY_TYPE_LABEL.get((r.get("type") or "").strip().lower())
            out.append(f"{name} ({label})" if label else name)
        return out[:6]
    except Exception:
        log.debug("briefing: entity descriptor lookup failed", exc_info=True)
        return []


def _prior_coverage(text: str, digest_date: date_t, seen: set) -> list[str]:
    """Dated one-liners from EARLIER digests about the same entities, for continuity ('this builds
    on…'). Already-vetted bullets → grounded. ``seen`` dedupes across the whole briefing so the same
    earlier item isn't fed under two stories. Best-effort — returns [] on any failure."""
    if not config.BRIEFING_PRIOR_DAYS:
        return []
    try:
        from . import entities  # lazy: heavy module, render-time only

        slugs = entities.detect_entities_in_text(text)
        if not slugs:
            return []
        terms: list[str] = []
        for r in db.get_entities_by_slugs(slugs):
            name = (r.get("name") or "").strip()
            if name:
                terms.append(name)
            for alias in (r.get("aliases") or []):
                if alias and len(alias) >= 4:
                    terms.append(alias)
        if not terms:
            return []
        rows = db.get_prior_bullets_matching_terms(
            terms, digest_date, days=config.BRIEFING_PRIOR_DAYS,
            limit=config.BRIEFING_PRIOR_MAX * 4)
        out = []
        for row in rows:
            title = (row.get("title") or "").strip()
            key = (row["digest_date"], title)
            if not title or key in seen:
                continue
            seen.add(key)
            day = row["digest_date"].strftime("%b %d").replace(" 0", " ")
            out.append(f"{day}: {title}")
            if len(out) >= config.BRIEFING_PRIOR_MAX:
                break
        return out
    except Exception:
        log.debug("briefing: prior-coverage lookup failed", exc_info=True)
        return []


def _collect_facts(text: str) -> list[str]:
    """Authoritative known-facts + generic pre-launch modality warnings for entities in the text
    (same grounding the thread uses). Best-effort — returns [] on any failure."""
    try:
        from . import entities, known_facts, audit  # lazy: heavy modules, render-time only

        facts = known_facts.facts_for_slugs(entities.detect_entities_in_text(text))
        for f in known_facts.facts_for_text(text):
            if f not in facts:
                facts.append(f)
        for f in audit.prelaunch_warnings(text):
            if f not in facts:
                facts.append(f)
        return facts
    except Exception:
        log.debug("briefing: fact collection failed", exc_info=True)
        return []


def _build_signals(bullets: list[dict], digest_date: date_t) -> list[dict]:
    """Enrich each ordered bullet into a grounded signal (facts + entity descriptors + prior
    coverage). Built ONCE per date and shared across all length variants — the enrichment is
    identical regardless of length, so the three shows render from exactly the same notes."""
    signals: list[dict] = []
    seen_prior: set = set()
    for b in bullets:
        text = f"{b['title']} {b.get('body', '')}"
        signals.append({
            "title": b["title"],
            "body": b.get("body", ""),
            "analysis": b.get("analysis", ""),
            "facts": _collect_facts(text),
            "entities": _entity_descriptors(text),
            "prior": _prior_coverage(text, digest_date, seen_prior),
        })
    return signals


def _turns_word_count(turns: list[tuple[str, str, int]]) -> int:
    """Spoken word count of a parsed script (labels + chapter markers excluded)."""
    return sum(len(txt.split()) for _, txt, _ in turns)


def _labeled_transcript(turns: list[tuple[str, str, int]], titles: list[str]) -> str:
    """Reconstruct the HOST:/EXPERT: + '## title' script from parsed turns, to feed an expand pass
    back to the model (the on-air-name ``_format_transcript`` is for storage, not re-prompting)."""
    out: list[str] = []
    last_ch = -1
    for sp, txt, ch in turns:
        if ch != last_ch and 0 < ch < len(titles):
            out.append(f"## {titles[ch]}")
            last_ch = ch
        out.append(f"{sp}: {txt}")
    return "\n".join(out)


# Fresh-draft retries granted to EVERY variant when a draft yields zero usable turns (all
# leak-dropped, or an empty completion). Floor variants already re-attempted via their expand
# rounds; the floorless 'short' flash had NO second chance — one leaked draft and the whole
# variant silently skipped (2026-07-07, the missing short show).
_EMPTY_DRAFT_RETRIES = 2
# Pause before retrying after a full-chain LLM failure: the OpenRouter free tier 429s with
# Retry-After ≈17s, so 25s clears the advertised window without stalling the render loop.
_LLM_FAIL_BACKOFF_SEC = 25
# Words the max_tokens budget can actually emit: content costs ~2.5 tok/word + speaker labels and
# chapter markers on top (config sizes max_tokens at ceiling × 2.6); the stated ask uses a more
# conservative 2.9 so a fully-compliant draft still finishes with a close instead of truncating.
_ASK_TOKENS_PER_WORD = 2.9


def _ask_words(spec: dict, min_words: int) -> int:
    """Word target to STATE in the prompt for one variant. Floor variants overshoot the enforced
    floor by BRIEFING_ASK_OVERSHOOT: models habitually undershoot a long stated target, so a
    prompt that asks for exactly the floor makes the floor the model's ceiling of effort and an
    under-floor draft's expand passes plateau just short of it (the 1170-word / 7-minute deep
    dive of 2026-07-15). Asking past the floor makes the habitual undershoot land ON it. Capped
    by the variant's hard ceiling and by what ``max_tokens`` can emit, so overshooting never
    trades the short-draft problem for a truncated one. Enforcement is unchanged — the floor +
    expand loop in ``_build_script`` stays the lever (failure mode #27)."""
    ask = spec["target_words"]
    if min_words > 0:
        ask = max(ask, int(min_words * config.BRIEFING_ASK_OVERSHOOT))
        ceiling = spec.get("ceiling") or 0
        if ceiling > 0:
            ask = min(ask, ceiling)
        ask = min(ask, int(spec["max_tokens"] / _ASK_TOKENS_PER_WORD))
    return ask


def _build_script(date_label: str, signals: list[dict], variant: str,
                  min_words: int = 0) -> tuple[list[tuple[str, str, int]], list[str], str]:
    """LLM → (speech-normalized turns, chapter titles, model_used) for one length variant.
    Empty turns on failure. ``signals`` are already enriched + sliced to this variant's bullet count
    by the caller.

    ``min_words`` (>0) sets a word FLOOR: a single pass habitually undershoots a long target, so when
    a draft lands short we re-prompt to EXPAND it (no new facts) up to ``BRIEFING_EXPAND_ROUNDS``
    times, keeping the LONGEST valid draft. This is what stops the ~12-min deep dive from rendering
    shorter than the ~6-min standard show on a given day."""
    spec = config.BRIEFING_VARIANT_SPECS.get(variant, config.BRIEFING_VARIANT_SPECS["standard"])
    system = prompts.build_briefing_system(config.BRIEFING_HOST_NAME, config.BRIEFING_EXPERT_NAME,
                                           variant)
    ask = _ask_words(spec, min_words)   # stated target overshoots the enforced floor (see helper)
    user = prompts.build_briefing_user(date_label, ask, signals,
                                       config.BRIEFING_HOST_NAME, config.BRIEFING_EXPERT_NAME,
                                       variant)

    best_turns: list[tuple[str, str, int]] = []
    best_titles: list[str] = []
    best_model = ""
    best_wc = -1
    best_truncated = True   # unset; any real draft is at least as good
    base_user = user        # the expand pass rewrites `user`; a truncation retry reuses the base
    rounds = 1 + max(config.BRIEFING_EXPAND_ROUNDS if min_words > 0 else 0, _EMPTY_DRAFT_RETRIES)
    for attempt in range(rounds):
        try:
            content, model, finish = llm.complete_ex(system, user, max_tokens=spec["max_tokens"],
                                                     temperature=0.5, skip_reasoning=True,
                                                     timeout=config.BRIEFING_LLM_TIMEOUT_SEC)
        except Exception:
            # A chain exhaustion consumes ONE attempt, it does not abandon the variant: the free
            # fallbacks 429 with Retry-After ≈17s, so a short pause + retry usually lands where an
            # immediate abort left the whole show unrendered (2026-07-15: six identical heal
            # failures, each giving up on its first and only call).
            log.warning("briefing[%s]: LLM call failed (attempt %d/%d)", variant, attempt + 1,
                        rounds, exc_info=True)
            if attempt + 1 < rounds:
                time.sleep(_LLM_FAIL_BACKOFF_SEC)
            continue
        truncated = finish == "length"  # hit max_tokens → cut off mid-sentence, no sign-off
        turns, titles = _speaker_turns(llm.strip_think(content))
        wc = _turns_word_count(turns)
        # Prefer a COMPLETE draft over a truncated one; only then does length break the tie. A
        # truncated draft is longer purely because it was cut at the token ceiling — never let that
        # length win over a cleanly-finished draft (the bug that shipped the mid-sentence show).
        # An EMPTY draft (every turn leak-dropped) never becomes "best": with finish='stop' it
        # would read as complete-with-0-words, beating a later truncated-but-real draft AND
        # satisfying the floorless break below — the two holes behind the silent short-flash skip.
        if turns and (best_wc < 0 or (best_truncated and not truncated)
                or (best_truncated == truncated and wc > best_wc)):
            best_turns, best_titles, best_model, best_wc, best_truncated = (
                turns, titles, model, wc, truncated)
        if not turns:
            log.warning("briefing[%s]: draft produced no usable turns (attempt %d/%d)",
                        variant, attempt + 1, rounds)
        # A truncated draft never satisfies the floor — it stopped at the token ceiling, not a close.
        if best_turns and best_wc >= min_words and not best_truncated:
            break
        if attempt + 1 < rounds and best_turns:
            if best_truncated:
                # Expanding a truncated draft only truncates harder — retry the base prompt instead
                # (temperature 0.5 gives a fresh draft that may finish within budget).
                log.info("briefing[%s]: draft truncated at max_tokens (%d words) — retry %d/%d",
                         variant, best_wc, attempt + 1, rounds - 1)
                user = base_user
            else:
                log.info("briefing[%s]: %d words < floor %d — expand pass %d/%d (asking %d)",
                         variant, best_wc, min_words, attempt + 1, rounds - 1, ask)
                # The expand pass states the OVERSHOT ask too, not the bare floor — a pass that
                # re-asks for exactly the floor tends to land just under it (see _ask_words).
                user = prompts.build_briefing_expand_user(
                    date_label, _labeled_transcript(best_turns, best_titles), best_wc, ask,
                    signals, config.BRIEFING_HOST_NAME, config.BRIEFING_EXPERT_NAME)
    if best_truncated and best_turns:
        log.warning("briefing[%s]: best draft was truncated at max_tokens (%d words) — "
                    "_finalize_close will trim + re-close it", variant, best_wc)
    if min_words > 0 and 0 <= best_wc < min_words:
        log.warning("briefing[%s]: still under floor after %d attempt(s) (%d/%d words)",
                    variant, rounds, best_wc, min_words)

    # Chapter-marker repair: dialogue variants need >=2 chapter titles for in-player navigation,
    # but the model sometimes drops the '##' markers entirely (every turn collapses into the intro
    # chapter → no chapters at all). One cheap, narrow pass re-inserts the markers into its own
    # script without touching the spoken lines, so navigation is reliable without re-rendering.
    if variant != "short" and best_turns and len(best_titles) < 2:
        try:
            content, _ = llm.complete(
                prompts.BRIEFING_CHAPTER_REPAIR_SYSTEM,
                prompts.build_briefing_chapter_repair_user(
                    _labeled_transcript(best_turns, best_titles)),
                max_tokens=spec["max_tokens"], temperature=0.2, skip_reasoning=True,
                timeout=config.BRIEFING_LLM_TIMEOUT_SEC)
            r_turns, r_titles = _speaker_turns(llm.strip_think(content))
            r_wc = _turns_word_count(r_turns)
            # Adopt only if it actually added markers AND preserved the words (no rewrite/trim).
            if len(r_titles) >= 2 and r_wc >= best_wc * 0.95:
                best_turns, best_titles = r_turns, r_titles
                log.info("briefing[%s]: chapter-marker repair added %d chapters", variant,
                         len(r_titles) - 1)
            else:
                log.warning("briefing[%s]: chapter-marker repair ineffective (titles=%d, "
                            "words %d->%d)", variant, len(r_titles), best_wc, r_wc)
        except Exception:
            log.warning("briefing[%s]: chapter-marker repair pass failed", variant, exc_info=True)

    return best_turns, best_titles, best_model


# Canonical sign-off lines to re-attach when a trim/truncation drops the scripted close, so the show
# always ends cleanly (mirrors the sign-offs in prompts.build_briefing_system, already in spoken
# form). Keyed by variant — one per variant, because `_finalize_close` guarantees a clean close on
# ALL of them (not just the ceiling-trimmed standard show).
_SIGN_OFF = {
    "short": "That's the Flash. The full briefing and feed are at Horyon dot X Y Z.",
    "standard": "That's your Horyon briefing. The full feed and analysis are at Horyon dot X Y Z.",
    "explainer": "That's your Horyon Deep Dive. The full feed and analysis are at Horyon dot X Y Z.",
}
# Does a turn already carry the scripted sign-off? Matches the distinctive close phrasing the prompt
# uses (see prompts.py `_build_briefing_system_*`) so `_finalize_close` never double-signs a show the
# model already closed. Kept phrase-anchored (not just "Horyon") so a mid-show mention can't match.
_SIGNOFF_MARK_RE = re.compile(
    r"That'?s (?:your|the) Horyon|That'?s the Flash|Horyon dot X Y Z", re.IGNORECASE)
# Sentence-final punctuation (optionally trailed by a closing quote/bracket) at end of a turn.
_SENT_END_RE = re.compile(r"[.!?][\"'”’)\]]*\s*$")
# A sentence terminator that is FOLLOWED by whitespace (a real boundary) — used to find where the
# last complete sentence ends so a dangling fragment can be cut. The look-ahead on whitespace keeps
# it from firing on a decimal point ("2.5") or an abbreviation mid-number.
_SENT_BOUNDARY_RE = re.compile(r"[.!?][\"'”’)\]]*(?=\s)")


def _has_signoff(text: str) -> bool:
    return bool(_SIGNOFF_MARK_RE.search(text or ""))


def _trim_partial_sentence(text: str) -> str:
    """Drop a dangling, incomplete final sentence from ``text`` (a max_tokens truncation, a dropped
    stream, or a model that just stopped mid-thought). Keeps everything through the LAST complete
    sentence; returns '' if the turn contains no complete sentence at all (so the caller drops the
    whole fragment turn)."""
    t = (text or "").rstrip()
    if not t or _SENT_END_RE.search(t):
        return t  # empty, or already ends on a complete sentence — leave it
    last = None
    for m in _SENT_BOUNDARY_RE.finditer(t):
        last = m.end()
    return t[:last].rstrip() if last else ""


def _finalize_close(turns: list[tuple[str, str, int]], titles: list[str],
                    variant: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    """Deterministic safety net (runs for EVERY variant, every render): guarantee the show ends on a
    complete sentence AND carries its canonical sign-off. First trims/drops any dangling final
    fragment (never leave a mid-sentence tail — the truncation-bug symptom); then, if the surviving
    close isn't already the scripted sign-off, appends it in the HOST voice on the last chapter.
    Idempotent — a script that already ends cleanly with its sign-off passes through unchanged."""
    turns = list(turns)
    # 1. Peel/trim a dangling final fragment so the last spoken line is a complete sentence.
    while turns:
        sp, txt, ch = turns[-1]
        trimmed = _trim_partial_sentence(txt)
        if trimmed:
            if trimmed != txt:
                turns[-1] = (sp, trimmed, ch)
            break
        turns.pop()
    # 2. Guarantee the canonical close.
    sign_off = _SIGN_OFF.get(variant)
    if turns and sign_off and not _has_signoff(turns[-1][1]):
        turns.append(("HOST", sign_off, turns[-1][2]))
    if turns:
        titles = titles[:max(ch for _, _, ch in turns) + 1]
    return turns, titles


def _enforce_ceiling(turns: list[tuple[str, str, int]], titles: list[str], ceiling: int,
                     variant: str) -> tuple[list[tuple[str, str, int]], list[str]]:
    """Hard UPPER bound on spoken length (the deterministic complement to the word FLOOR). If the
    rendered script exceeds ``ceiling`` words, drop whole trailing STORY chapters — never mid-exchange
    — keeping the intro (ch 0) + at least the lead story (ch 1), then re-attach a clean canonical
    sign-off so the show still closes. No-op when ``ceiling`` <= 0 or the script already fits.
    Returns the (possibly trimmed) ``(turns, titles)``; chapter indices on the kept turns are
    unchanged so downstream chapter/signal alignment still holds."""
    if ceiling <= 0 or not turns or _turns_word_count(turns) <= ceiling:
        return turns, titles
    sign_off = _SIGN_OFF.get(variant)
    reserve = (len(sign_off.split()) + 1) if sign_off else 0
    keep_ch = max(ch for _, _, ch in turns)
    # Peel trailing story chapters until it fits (leaving room for the sign-off), but never below
    # intro + lead story.
    while keep_ch > 1:
        kept = [t for t in turns if t[2] <= keep_ch]
        if _turns_word_count(kept) + reserve <= ceiling:
            break
        keep_ch -= 1
    kept = [t for t in turns if t[2] <= keep_ch]
    dropped = max(ch for _, _, ch in turns) - keep_ch
    if sign_off and (not kept or kept[-1][1].strip() != sign_off):
        kept.append(("HOST", sign_off, keep_ch))
    titles = titles[:keep_ch + 1]
    log.info("briefing[%s]: ceiling trim — %d words over %d, dropped %d trailing chapter(s) "
             "→ %d words", variant, _turns_word_count(turns), ceiling, dropped,
             _turns_word_count(kept))
    return kept, titles


def _failed_marker_allowed(existing: "dict | None") -> bool:
    """Whether a failed render may (re)write its 'failed' marker row. True when the variant has
    no row yet or only a previous 'failed' marker — re-writing refreshes created_at so the heal
    spacing gate (variants_needing_heal) measures from the LAST attempt, not the first. False for
    ready/pending/blocked: a finished, in-flight, or fail-closed row is never clobbered by a
    later failed re-render."""
    return existing is None or existing.get("status") == "failed"


def _variant_floor(variant: str, min_words_override: int = 0) -> int:
    """Word floor for a variant: 0 for variants that don't enforce one (the flash is a ceiling), else
    ``target_words * BRIEFING_MIN_WORD_RATIO``. ``min_words_override`` (e.g. the standard show's
    actual length × BRIEFING_EXPLAINER_OVER_STANDARD) raises it further so the deep dive can't land
    at or below the briefing it deepens."""
    spec = config.BRIEFING_VARIANT_SPECS.get(variant, config.BRIEFING_VARIANT_SPECS["standard"])
    if not spec.get("floor"):
        return max(0, min_words_override)
    return max(int(spec["target_words"] * config.BRIEFING_MIN_WORD_RATIO), min_words_override)


def _render_variant(digest_date: date_t, date_label: str, signals: list[dict], variant: str,
                    persist: bool, synth: bool, min_words: int = 0) -> "dict | None":
    """Build ONE length variant from already-prepared signals: script → modality gate → optional
    TTS → optional persist. Returns the briefing dict (no audio bytes) or None when the script
    can't be produced. Shared by the single-variant and all-variants entry points. ``min_words``
    is the word floor passed to ``_build_script`` (see ``_variant_floor``)."""
    spec = config.BRIEFING_VARIANT_SPECS.get(variant, config.BRIEFING_VARIANT_SPECS["standard"])
    sigs = signals[:spec["max_bullets"]]
    if not sigs:
        return None

    turns, titles, model = _build_script(date_label, sigs, variant, min_words)
    # Hard upper bound: trim an over-long draft down to the variant ceiling BEFORE the word count,
    # modality gate, and TTS run on it, so the stored script, audio, and chapters all reflect the
    # trimmed show (keeps "the briefing" under ~9 minutes).
    turns, titles = _enforce_ceiling(turns, titles, spec.get("ceiling", 0), variant)
    # Guarantee a clean close on EVERY variant: no mid-sentence tail, always the canonical sign-off.
    # This is the deterministic backstop under the raised max_tokens + truncation detection, so even
    # a residual cut-off draft (or a dropped TTS stream) can never ship a show that ends mid-word.
    turns, titles = _finalize_close(turns, titles, variant)
    # Plain text (no speaker labels) drives the word count + the modality gate; the labeled
    # transcript is what we store and what the CLI prints.
    plain = " ".join(txt for _, txt, _ in turns)
    word_count = len(plain.split())
    if not turns or word_count < 40:
        log.warning("briefing[%s]: empty/too-short script for %s (%d words) — skipping",
                    variant, digest_date, word_count)
        existing = db.get_audio_briefing(digest_date, variant) if persist else None
        if persist and _failed_marker_allowed(existing):
            # Durable 'failed' marker (no audio) so the miss surfaces in pipeline_check §10 and
            # stays retryable by --backfill (the audio backfill queries skip status='failed').
            # Re-written on EVERY failed attempt so created_at tracks the LAST attempt — the
            # 75-min heal spacing gate (variants_needing_heal) keys off it, and a frozen
            # first-failure timestamp let the heal re-fire every 20-min cycle (2026-07-15).
            # A ready/pending/blocked row is never overwritten (_failed_marker_allowed).
            db.upsert_audio_briefing(digest_date=digest_date, variant=variant,
                                     script=_format_transcript(turns) if turns else "",
                                     model_used=model, status="failed")
        return None
    script = _format_transcript(turns)

    # PRE-SYNTH SAFETY GATE — audio is outward-facing and irreversible once sent, so if any
    # sentence asserts a pre-launch/testnet entity as already LIVE (the Arc failure mode), fail
    # CLOSED: store the script 'blocked' and never synthesize or send it. Same sentence-level
    # modality check the thread pre-publish gate uses — applied identically to every variant.
    from . import audit
    violations = audit.modality_violations(plain, audit.compile_prelaunch_patterns())

    audio = engine = None
    mime = "audio/mpeg"
    duration = byte_size = None
    voice_label = ""
    chapters: list[dict] = []
    if violations:
        status = "blocked"
        log.error("briefing[%s] gate: %s BLOCKED (status=blocked, not synthesized) — %d modality "
                  "violation(s): %s", variant, digest_date, len(violations), violations[0][1][:140])
    elif not synth:
        status = "pending"
    else:
        # Two-voice podcast when the variant allows it, edge is active, and the script actually has
        # an expert turn (the 'short' flash is single-voice by design); otherwise (or if the stitch
        # refuses on a mixed engine) a single-voice read. Only the two-voice path yields per-turn
        # durations, so chapters ride on it.
        use_dialogue = (spec["dialogue"] and config.BRIEFING_DIALOGUE
                        and config.TTS_ENGINE == "edge"
                        and any(sp == "EXPERT" for sp, _, _ in turns))
        try:
            if use_dialogue:
                try:
                    audio, mime, engine, part_durs = tts.synthesize_dialogue(
                        [(sp, txt, ch) for sp, txt, ch in turns],
                        {"HOST": config.TTS_HOST_VOICE, "EXPERT": config.TTS_EXPERT_VOICE})
                    voice_label = f"{config.TTS_HOST_VOICE} + {config.TTS_EXPERT_VOICE}"
                    chapters = _build_chapters(turns, titles, part_durs, sigs)
                except Exception:
                    log.warning("briefing[%s]: dialogue synth failed for %s — single-voice "
                                "fallback", variant, digest_date, exc_info=True)
                    audio = None
            if audio is None:
                # Single voice: the flash is read in the HOST voice; a dialogue fallback keeps the
                # default narrator voice. The mono path has no per-turn timing → no chapters.
                mono_voice = config.TTS_HOST_VOICE if variant == "short" else config.TTS_VOICE
                audio, mime, engine = tts.synthesize(plain, voice=mono_voice)
                voice_label = mono_voice if engine == "edge" else config.PIPER_VOICE
                chapters = []
            duration = tts.estimate_duration(audio, mime, word_count)
            audio = tts.embed_id3_tags(
                audio,
                title=(f"Horyon {tts._VARIANT_DISPLAY.get(variant, 'Briefing')} · "
                       f"{digest_date.strftime('%b %d, %Y').replace(' 0', ' ')}"),
                date_str=str(digest_date),
            )
            byte_size = len(audio)
            status = "ready"
        except Exception:
            status = "failed"
            log.warning("briefing[%s]: TTS synthesis failed for %s (script kept)", variant,
                        digest_date, exc_info=True)

    if persist:
        waveform = tts.compute_waveform(script) if script else None
        db.upsert_audio_briefing(
            digest_date=digest_date, variant=variant, script=script, audio=audio, mime=mime,
            voice=voice_label, tts_engine=engine or "", duration_sec=duration, byte_size=byte_size,
            word_count=word_count, model_used=model, status=status, chapters=chapters or None,
            waveform=waveform,
        )
        log.info("briefing[%s]: stored %s (status=%s, %d words, %ss, %d chapters, engine=%s)",
                 variant, digest_date, status, word_count, duration or "?", len(chapters),
                 engine or "-")

    return {
        "digest_date": digest_date, "variant": variant, "script": script, "status": status,
        "voice": voice_label, "tts_engine": engine, "duration_sec": duration,
        "word_count": word_count, "byte_size": byte_size, "model_used": model,
    }


def build_briefing_for_date(digest_date: date_t | None = None, persist: bool = True,
                            synth: bool = True, variant: str = "standard") -> dict | None:
    """Build (and optionally store) ONE length ``variant`` of the audio briefing for a digest date.

    Returns the briefing dict (without the audio bytes), or None when the date has no bullets or
    the script can't be produced. ``synth=False`` builds the script only (preview / debug).
    """
    digest_date = digest_date or datetime.now(timezone.utc).date()

    # Reuse the thread's bullet ordering (single source of truth: grounded + importance-ranked).
    from . import threads
    bullets = threads._ordered_bullets(digest_date)
    if not bullets:
        log.info("briefing: no bullets for %s — skipping", digest_date)
        return None
    spec = config.BRIEFING_VARIANT_SPECS.get(variant, config.BRIEFING_VARIANT_SPECS["standard"])
    signals = _build_signals(bullets[:spec["max_bullets"]], digest_date)
    return _render_variant(digest_date, _date_label(digest_date), signals, variant, persist, synth,
                           _variant_floor(variant))


def build_all_variants_for_date(digest_date: date_t | None = None, persist: bool = True,
                                synth: bool = True,
                                variants: "tuple[str, ...] | None" = None) -> dict:
    """Build every configured length variant (short/standard/explainer) for a date in one pass.

    The grounded signals are enriched ONCE (for the largest variant's bullet count) and sliced per
    variant, so the three shows cost one set of entity/fact lookups, not three. ``variants`` limits
    the build to a subset (used by ``--backfill`` to render only the missing ones). Returns
    ``{variant: result_dict_or_None}``; ``{}`` when the date has no bullets.
    """
    digest_date = digest_date or datetime.now(timezone.utc).date()
    variants = tuple(variants) if variants else config.BRIEFING_VARIANTS
    variants = tuple(v for v in variants if v in config.BRIEFING_VARIANT_SPECS)
    if not variants:
        return {}

    from . import threads
    bullets = threads._ordered_bullets(digest_date)
    if not bullets:
        log.info("briefing: no bullets for %s — skipping all variants", digest_date)
        return {}
    max_needed = max(config.BRIEFING_VARIANT_SPECS[v]["max_bullets"] for v in variants)
    signals = _build_signals(bullets[:max_needed], digest_date)
    date_label = _date_label(digest_date)
    # Render shortest → longest so the standard show's actual length is known when we set the
    # explainer's floor — the deep dive must clear standard_words × BRIEFING_EXPLAINER_OVER_STANDARD
    # so it can never come out at or below the briefing it deepens (even on a quiet day).
    out: dict = {}
    # Seed from any already-stored standard show so an explainer-only backfill still clears it.
    standard_wc = 0
    if "explainer" in variants and "standard" not in variants:
        existing = db.get_audio_briefing(digest_date, "standard")
        standard_wc = (existing or {}).get("word_count") or 0
    for v in sorted(variants, key=lambda x: config.BRIEFING_VARIANT_SPECS[x]["target_words"]):
        override = 0
        if v == "explainer" and standard_wc > 0:
            override = int(standard_wc * config.BRIEFING_EXPLAINER_OVER_STANDARD)
        r = _render_variant(digest_date, date_label, signals, v, persist, synth,
                            _variant_floor(v, override))
        out[v] = r
        if v == "standard" and r:
            standard_wc = r.get("word_count") or 0
    return out


# ── Same-day AUDIO self-heal (mirrors digest.should_retry_digest / failure mode #31) ─────────
# The 07:00 post-digest builds all three length variants in one pass, but a transient LLM outage
# at that minute (NIM timeout on the heavy explainer + OpenRouter 429s) can leave a variant
# 'failed' with an EMPTY script — most often the explainer, whose ~2400-word two-voice draft is the
# biggest generation in the pipeline (observed 2026-07-08..10: standard/short rendered, explainer
# failed, so the site showed only the "Briefing" length). Nothing retried it: `build_all_variants`
# swallows per-variant failure (stores the durable 'failed' marker) so orchestrate_post_digest's
# run_step sees success, and the digest self-heal only fires when the whole day is empty. This is
# the missing backstop — the 20-min ingest cycle re-renders ONLY the missing/failed variants later
# in the day, when the models are reachable again (a manual rebuild of those days succeeds). Pure
# decision here; the effecting side + Telegram late-send live in main._maybe_heal_audio.
AUDIO_HEAL_MIN_GAP_MIN = 75      # a 'failed' variant is retried only once its last attempt is this old
AUDIO_HEAL_EARLIEST_UTC = 8      # never before 08:00 — the 07:00 build + its run_step retries own that hour
AUDIO_HEAL_LATEST_UTC = 22       # stop late in the day — a variant healed near midnight helps no one for "today"


def variants_needing_heal(
    rows: "list[dict]",
    bullets_exist: bool,
    now_utc: datetime,
    *,
    variants: "tuple[str, ...]" = config.BRIEFING_VARIANTS,
    gap_min: int = AUDIO_HEAL_MIN_GAP_MIN,
    earliest_utc: int = AUDIO_HEAL_EARLIEST_UTC,
    latest_utc: int = AUDIO_HEAL_LATEST_UTC,
) -> "tuple[str, ...]":
    """Pure decision: which audio variants for a date should be (re)built right now.

    ``rows`` = today's ``digest_audio`` rows as dicts (variant, status, created_at), any order —
    see ``db.get_audio_variant_status``. ``bullets_exist`` gates the whole thing: no digest bullets
    means there is nothing to narrate (and the digest self-heal, not this, owns a missing digest).
    A variant needs healing when it has NO row, or a 'failed' row whose last attempt is ≥ ``gap_min``
    minutes old. 'ready'/'pending' (done/in-flight) and 'blocked' (terminal fail-closed modality
    verdict) are never re-rendered. Returns () outside the [earliest, latest) UTC window or when
    nothing qualifies. The gap gate bounds cost: at most one retry per variant per ``gap_min``."""
    if not bullets_exist:
        return ()
    if not (earliest_utc <= now_utc.hour < latest_utc):
        return ()
    by_variant = {r["variant"]: r for r in rows}
    need: list[str] = []
    for v in variants:
        r = by_variant.get(v)
        if r is None:
            need.append(v)                       # never attempted this variant
        elif r.get("status") == "failed":
            last = r.get("created_at")
            if last is None:
                need.append(v)
                continue
            if now_utc - util.as_utc(last) >= timedelta(minutes=gap_min):
                need.append(v)
        # ready / pending / blocked → leave alone
    return tuple(need)


def _print_variant(r: "dict | None", variant: str) -> None:
    if r is None:
        print(f"\n=== {variant}: skipped (no bullets/script) ===")
        return
    print(f"\n=== Horyon {variant} briefing {r['digest_date']} "
          f"({r['word_count']} words, status={r['status']}, "
          f"{r['duration_sec'] or '?'}s) ===\n")
    print(r["script"])


def _summarize(res: dict, keys) -> str:
    out = []
    for v in keys:
        r = res.get(v)
        out.append(f"{v}=skip" if r is None else f"{v}={r['status']}/{r['word_count']}w")
    return ", ".join(out) if out else "(all present)"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(
        description="Render a digest into spoken audio briefings (short / standard / explainer).")
    ap.add_argument("--date", metavar="YYYY-MM-DD", help="build for one date (default: today)")
    ap.add_argument("--variant", choices=["short", "standard", "explainer", "all"], default="all",
                    help="which length to build (default: all three)")
    ap.add_argument("--no-persist", action="store_true",
                    help="print the script only; no DB write, no audio synthesis")
    ap.add_argument("--no-audio", action="store_true",
                    help="build + store the script but skip TTS (status stays 'pending')")
    ap.add_argument("--backfill", action="store_true",
                    help="build for every digest date missing one or more of the chosen variants")
    args = ap.parse_args()

    persist = not args.no_persist
    do_synth = not (args.no_audio or args.no_persist)
    want = config.BRIEFING_VARIANTS if args.variant == "all" else (args.variant,)

    if args.backfill:
        dates = db.get_digest_dates_without_audio(want)
        print(f"Backfilling audio briefings ({', '.join(want)}) for {len(dates)} date(s)…")
        for d in dates:
            have = db.get_existing_audio_variants(d)
            missing = tuple(v for v in want if v not in have)
            res = build_all_variants_for_date(d, persist=persist, synth=do_synth, variants=missing)
            print(f"  {d}: {_summarize(res, missing) if res else 'skipped (no bullets/script)'}")
        print("Done.")
    else:
        d = date_t.fromisoformat(args.date) if args.date else None
        if args.variant == "all":
            res = build_all_variants_for_date(d, persist=persist, synth=do_synth)
            if not res:
                print("No briefing — no digest bullets for that date.")
            else:
                for v in want:
                    _print_variant(res.get(v), v)
        else:
            r = build_briefing_for_date(d, persist=persist, synth=do_synth, variant=args.variant)
            if r is None:
                print("No briefing — no digest bullets for that date (or script failed).")
            else:
                _print_variant(r, args.variant)
