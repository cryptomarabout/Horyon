"""DeFiLlama free public API integration (no key required).

Jobs:
  fetch_and_store()          — daily chain TVL snapshot  (upserts defillama_tvl)
  fetch_and_store_protocols()— every 2 h protocol TVLs   (upserts defillama_protocols)
"""
from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.request

from . import db

log = logging.getLogger(__name__)

BASE_URL = "https://api.llama.fi"
TIMEOUT = 30

# --- chain TVL ---
CHAINS = ["Ethereum", "Solana", "Base", "Arbitrum", "BSC", "Tron"]

# --- protocol TVL ---
# Exclude pure CEX (Binance, OKX, etc.) — not DeFi protocols.
# No chain filter: keeps cross-chain protocols like THORChain, deBridge, etc.
EXCLUDED_CATEGORIES = {"CEX"}
PROTOCOL_TOP_N = 1000


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def _get(path: str) -> list | dict | None:
    url = BASE_URL + path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Horyon/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 402:
            log.warning("defillama %s → HTTP 402 (endpoint requires a paid DeFiLlama plan)", path)
        else:
            log.warning("defillama %s → HTTP %s", path, exc.code)
    except Exception:
        log.exception("defillama fetch failed: %s", path)
    return None


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Chain TVL (daily)
# --------------------------------------------------------------------------- #
def _latest_point(path: str) -> tuple[datetime.date, float] | None:
    data = _get(path)
    if not data or not isinstance(data, list):
        return None
    last = data[-1]
    date = datetime.date.fromtimestamp(last["date"])
    return date, float(last["tvl"])


def fetch_and_store() -> int:
    """Fetch latest TVL for total DeFi + tracked chains; upsert. Returns row count."""
    rows: list[tuple[datetime.date, str, float]] = []

    result = _latest_point("/v2/historicalChainTvl")
    if result:
        rows.append((result[0], "total", result[1]))

    for chain in CHAINS:
        result = _latest_point(f"/v2/historicalChainTvl/{chain}")
        if result:
            rows.append((result[0], chain, result[1]))
        time.sleep(0.3)

    if not rows:
        log.warning("defillama: no TVL rows fetched")
        return 0

    db.upsert_tvl(rows)
    log.info("defillama: stored %d TVL rows (chains: %s)",
             len(rows), [r[1] for r in rows])
    return len(rows)


# --------------------------------------------------------------------------- #
# Protocol TVL (every 2 h)
# --------------------------------------------------------------------------- #
def _extract_chain_tvls(p: dict) -> dict:
    """Extract per-chain current TVL (USD) from a DeFiLlama protocol record."""
    raw = p.get("currentChainTvls") or {}
    if isinstance(raw, dict) and raw:
        return {k: float(v) for k, v in raw.items()
                if isinstance(v, (int, float)) and v > 0}
    raw = p.get("chainTvls") or {}
    result = {}
    for chain, val in raw.items():
        if isinstance(val, (int, float)) and val > 0:
            result[chain] = float(val)
        elif isinstance(val, dict):
            tvl = val.get("tvl")
            if isinstance(tvl, (int, float)) and tvl > 0:
                result[chain] = float(tvl)
            elif isinstance(tvl, list) and tvl:
                last = tvl[-1]
                v = last.get("totalLiquidityUSD") or last.get("tvl") or 0
                if v > 0:
                    result[chain] = float(v)
    return result


def fetch_and_store_protocols() -> int:
    """Fetch /protocols; filter to target chains; upsert top 100 by TVL. Returns row count."""
    data = _get("/protocols")
    if not isinstance(data, list):
        log.warning("defillama protocols: unexpected response type %s", type(data))
        return 0

    filtered = [
        p for p in data
        if (p.get("category") or "") not in EXCLUDED_CATEGORIES
    ]
    filtered.sort(key=lambda p: float(p.get("tvl") or 0), reverse=True)
    top = filtered[:PROTOCOL_TOP_N]

    rows: list[dict] = []
    for p in top:
        slug = (p.get("slug") or "").strip()
        if not slug:
            continue
        rows.append({
            "slug": slug[:255],
            "name": (p.get("name") or "")[:255],
            "category": (p.get("category") or "")[:100],
            "chains": [str(c) for c in (p.get("chains") or []) if c][:30],
            "chain_tvls": _extract_chain_tvls(p),
            "tvl_usd": float(p.get("tvl") or 0),
            "tvl_change_1d": _safe_float(p.get("change_1d")),
            "tvl_change_7d": _safe_float(p.get("change_7d")),
            "mcap_tvl": _safe_float(p.get("mcaptvl")),
            "token_symbol": (p.get("symbol") or "")[:20],
            "logo_url": (p.get("logo") or "")[:500],
            "url": (p.get("url") or "")[:500],
            "description": (p.get("description") or "")[:500],
            "gecko_id": (p.get("gecko_id") or "")[:100],
        })

    if not rows:
        return 0

    db.upsert_protocols(rows)
    log.info("defillama protocols: upserted %d rows", len(rows))
    return len(rows)


# --------------------------------------------------------------------------- #
# CoinGecko entity seed (top N coins by market cap)
# --------------------------------------------------------------------------- #
_CG_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_TOP_N = 500


def _cg_get(path: str) -> list | dict | None:
    url = _CG_BASE + path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Horyon/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except Exception:
        log.exception("coingecko fetch failed: %s", path)
    return None


def fetch_and_seed_coingecko(top_n: int = COINGECKO_TOP_N) -> int:
    """Fetch top N coins from CoinGecko markets API and upsert into entity_memory.

    For existing entities: only fills missing logo_url and merges aliases.
    For new entities:      inserts with mention_count=1 (below display threshold
                           of 2, invisible until organically mentioned in feeds).
    """
    per_page = 250
    coins: list[dict] = []
    page = 1
    while len(coins) < top_n:
        batch = _cg_get(
            f"/coins/markets?vs_currency=usd&order=market_cap_desc"
            f"&per_page={per_page}&page={page}&sparkline=false"
        )
        if not batch:
            break
        coins.extend(batch)
        page += 1
        if len(coins) < top_n:
            time.sleep(2)   # free-tier rate limit: ~30 req/min

    coins = coins[:top_n]
    if not coins:
        log.warning("coingecko: no coins fetched")
        return 0

    seeded = 0
    for coin in coins:
        slug = (coin.get("id") or "").strip()
        name = (coin.get("name") or "").strip()
        symbol = (coin.get("symbol") or "").strip().lower()
        logo_url = (coin.get("image") or "").strip()
        if not slug or not name:
            continue
        # Build alias set: id, symbol, lowercased name
        aliases = list({slug, symbol, name.lower()} - {""})
        try:
            db.upsert_entity_from_coingecko(
                slug=slug,
                name=name,
                type_="protocol",
                aliases=aliases,
                logo_url=logo_url or None,
            )
            seeded += 1
        except Exception:
            log.warning("coingecko seed: failed for %s", slug, exc_info=True)

    log.info("coingecko seed: upserted %d / %d coins into entity_memory", seeded, len(coins))
    return seeded
