"""Market price + global metrics data.

Primary source: CoinMarketCap (if CMC_API_KEY is set).
Fallback: CoinGecko free API (no key required).

Returns normalised dicts so callers never know which source was used.
"""
from __future__ import annotations

import logging
import urllib.error
from typing import Any

from . import config, http

log = logging.getLogger(__name__)
TIMEOUT = 20


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get(url: str, headers: dict | None = None) -> Any:
    try:
        return http.get_json(url, headers=headers, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        log.warning("market data HTTP %s: %s", exc.code, url)
    except Exception:
        log.exception("market data fetch failed: %s", url)
    return None


# --------------------------------------------------------------------------- #
# CoinGecko (free, no API key)
# --------------------------------------------------------------------------- #
_CG_BASE = "https://api.coingecko.com/api/v3"


def _cg_top50() -> list[dict]:
    url = (
        f"{_CG_BASE}/coins/markets?vs_currency=usd&order=market_cap_desc"
        "&per_page=50&page=1&price_change_percentage=7d&sparkline=false"
    )
    data = _get(url)
    if not isinstance(data, list):
        return []
    out = []
    for i, c in enumerate(data):
        out.append({
            "rank":       i + 1,
            "symbol":     (c.get("symbol") or "").upper(),
            "name":       c.get("name") or "",
            "price":      c.get("current_price"),
            "market_cap": c.get("market_cap"),
            "change_7d":  c.get("price_change_percentage_7d_in_currency"),
            "change_24h": c.get("price_change_percentage_24h"),
        })
    return out


def _cg_global() -> dict:
    data = _get(f"{_CG_BASE}/global")
    if not data:
        return {}
    g = data.get("data", {})
    mcp = g.get("market_cap_percentage", {})
    mcaps = g.get("total_market_cap", {})
    return {
        "btc_dominance":        mcp.get("btc"),
        "eth_dominance":        mcp.get("eth"),
        "total_market_cap_usd": mcaps.get("usd"),
        "market_cap_change_24h_pct": g.get("market_cap_change_percentage_24h_usd"),
        "active_coins":         g.get("active_cryptocurrencies"),
        "defi_market_cap":      None,  # not directly in CoinGecko global
    }


# --------------------------------------------------------------------------- #
# CoinMarketCap (requires CMC_API_KEY)
# --------------------------------------------------------------------------- #
_CMC_BASE = "https://pro-api.coinmarketcap.com"


def _cmc_headers(key: str) -> dict:
    return {
        "User-Agent":      "Horyon/1.0",
        "X-CMC_PRO_API_KEY": key,
        "Accept":          "application/json",
    }


def _cmc_top50(key: str) -> list[dict]:
    url = (
        f"{_CMC_BASE}/v1/cryptocurrency/listings/latest"
        "?limit=50&sort=market_cap&sort_dir=desc&convert=USD"
    )
    data = _get(url, _cmc_headers(key))
    if not data or not isinstance(data.get("data"), list):
        return []
    out = []
    for c in data["data"]:
        q = c.get("quote", {}).get("USD", {})
        out.append({
            "rank":       c.get("cmc_rank"),
            "symbol":     c.get("symbol") or "",
            "name":       c.get("name") or "",
            "price":      q.get("price"),
            "market_cap": q.get("market_cap"),
            "change_7d":  q.get("percent_change_7d"),
            "change_24h": q.get("percent_change_24h"),
        })
    return out


def _cmc_global(key: str) -> dict:
    url = f"{_CMC_BASE}/v1/global-metrics/quotes/latest?convert=USD"
    data = _get(url, _cmc_headers(key))
    if not data:
        return {}
    d = data.get("data", {})
    q = d.get("quote", {}).get("USD", {})
    return {
        "btc_dominance":        d.get("btc_dominance"),
        "eth_dominance":        d.get("eth_dominance"),
        "total_market_cap_usd": q.get("total_market_cap"),
        "market_cap_change_24h_pct": q.get("total_market_cap_yesterday_percentage_change"),
        "active_coins":         d.get("active_cryptocurrencies"),
        "defi_market_cap":      q.get("defi_market_cap"),
    }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def fetch_market_data() -> dict:
    """Return {"top50": [...], "global": {...}}.

    Uses CMC when CMC_API_KEY is set, CoinGecko otherwise.
    On CMC failure falls back to CoinGecko.
    """
    key = config.CMC_API_KEY
    if key:
        log.info("market data: CoinMarketCap")
        top50  = _cmc_top50(key)
        global_ = _cmc_global(key)
        if not top50:
            log.warning("CMC top50 failed — falling back to CoinGecko")
            top50   = _cg_top50()
            global_ = _cg_global()
    else:
        log.info("market data: CoinGecko (no CMC_API_KEY)")
        top50   = _cg_top50()
        global_ = _cg_global()

    return {"top50": top50, "global": global_}
