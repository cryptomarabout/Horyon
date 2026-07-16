"""DeFiLlama free public API integration (no key required).

Jobs:
  fetch_and_store()          — daily chain TVL snapshot  (upserts defillama_tvl)
  fetch_and_store_protocols()— every 2 h protocol TVLs   (upserts defillama_protocols)
"""
from __future__ import annotations

import datetime
import logging
import time
import urllib.error

from . import db, http

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
        return http.get_json(url, timeout=TIMEOUT)
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
        return http.get_json(url, timeout=TIMEOUT)
    except Exception:
        log.exception("coingecko fetch failed: %s", path)
    return None


def _fetch_coingecko_markets(top_n: int, price_change_pct: str | None = None) -> list[dict]:
    """Paginate CoinGecko's /coins/markets (shared by the entity seed + the market-data
    snapshot below — both hit the same endpoint, just consume different fields)."""
    per_page = 250
    coins: list[dict] = []
    page = 1
    pct_param = f"&price_change_percentage={price_change_pct}" if price_change_pct else ""
    while len(coins) < top_n:
        batch = _cg_get(
            f"/coins/markets?vs_currency=usd&order=market_cap_desc"
            f"&per_page={per_page}&page={page}&sparkline=false{pct_param}"
        )
        if not batch:
            break
        coins.extend(batch)
        page += 1
        if len(coins) < top_n:
            time.sleep(2)   # free-tier rate limit: ~30 req/min
    return coins[:top_n]


def fetch_and_seed_coingecko(top_n: int = COINGECKO_TOP_N) -> int:
    """Fetch top N coins from CoinGecko markets API and upsert into entity_memory.

    For existing entities: only fills missing logo_url and merges aliases.
    For new entities:      inserts with mention_count=1 (below display threshold
                           of 2, invisible until organically mentioned in feeds).
    """
    coins = _fetch_coingecko_markets(top_n)
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


# --------------------------------------------------------------------------- #
# CoinGecko market-data snapshot (price / mcap / FDV / supply — analyst-grade facts)
# --------------------------------------------------------------------------- #
MARKET_DATA_TOP_N = 500


def fetch_and_store_market_data(top_n: int = MARKET_DATA_TOP_N) -> int:
    """Snapshot price/market-cap/FDV/supply for the top N coins by market cap into
    `coingecko_market`, keyed by CoinGecko's own coin id (`gecko_id`).

    This is DELIBERATELY a plain top-mcap snapshot rather than a per-entity lookup: it
    covers every major token in one bounded pair of API calls (~2 req, well under the
    free-tier rate limit), and joining it to an entity is left to the reader
    (`get_market_data_by_slugs` matches on `entity_memory.slug == gecko_id`, the same
    identity `fetch_and_seed_coingecko` already relies on for ~half of tracked
    entities — see docs/conventions.md). An entity whose slug doesn't happen to equal
    its CoinGecko id simply has no market row, mirroring the existing DeFiLlama TVL
    exact-slug lookup's known gap (brand aggregation is a web-side-only convenience).
    """
    coins = _fetch_coingecko_markets(top_n, price_change_pct="24h,7d")
    if not coins:
        log.warning("coingecko market data: no coins fetched")
        return 0

    rows = []
    for coin in coins:
        gecko_id = (coin.get("id") or "").strip()
        if not gecko_id:
            continue
        rows.append({
            "gecko_id": gecko_id,
            "symbol": (coin.get("symbol") or "").strip().upper(),
            "price_usd": _safe_float(coin.get("current_price")),
            "market_cap_usd": _safe_float(coin.get("market_cap")),
            "fdv_usd": _safe_float(coin.get("fully_diluted_valuation")),
            "circulating_supply": _safe_float(coin.get("circulating_supply")),
            "total_supply": _safe_float(coin.get("total_supply")),
            "market_cap_rank": coin.get("market_cap_rank"),
            "price_change_24h_pct": _safe_float(coin.get("price_change_percentage_24h_in_currency")),
            "price_change_7d_pct": _safe_float(coin.get("price_change_percentage_7d_in_currency")),
        })
    # CoinGecko paginates by LIVE market-cap rank; ranks shift between the page requests, so
    # the same coin can appear on two pages. A single multi-row upsert touching one key twice
    # is a Postgres CardinalityViolation (the recurring market-data cron crash, 2026-07-07) —
    # keep only the first (best-rank) occurrence per gecko_id.
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        if r["gecko_id"] in seen:
            continue
        seen.add(r["gecko_id"])
        deduped.append(r)
    rows = deduped
    if not rows:
        return 0
    db.upsert_market_data(rows)
    log.info("coingecko market data: stored %d / %d coins", len(rows), len(coins))
    return len(rows)
