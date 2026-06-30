"""Tests for app.defillama pure parsers — `_safe_float` and `_extract_chain_tvls`,
the record-shape normaliser that copes with DeFiLlama's several per-chain TVL layouts
(flat number, nested {tvl: number}, nested {tvl: [series]}). Network paths excluded.
"""
from __future__ import annotations

from app.defillama import _extract_chain_tvls, _safe_float


# ── _safe_float ──────────────────────────────────────────────────────────────

def test_safe_float_parses_numbers_and_strings():
    assert _safe_float("1.5") == 1.5
    assert _safe_float(5) == 5.0


def test_safe_float_none_and_bad_input():
    assert _safe_float(None) is None
    assert _safe_float("abc") is None
    assert _safe_float("") is None


# ── _extract_chain_tvls ──────────────────────────────────────────────────────

def test_extract_prefers_current_chain_tvls_and_drops_nonpositive():
    p = {"currentChainTvls": {"Ethereum": 100.0, "Base": 0, "Arbitrum": -5}}
    assert _extract_chain_tvls(p) == {"Ethereum": 100.0}


def test_extract_current_chain_tvls_all_nonpositive_short_circuits():
    # currentChainTvls is present+truthy → it wins even if everything filters out;
    # the chainTvls fallback is NOT consulted.
    p = {"currentChainTvls": {"X": 0}, "chainTvls": {"Y": 100}}
    assert _extract_chain_tvls(p) == {}


def test_extract_falls_back_to_flat_chain_tvls():
    p = {"chainTvls": {"Ethereum": 200}}
    assert _extract_chain_tvls(p) == {"Ethereum": 200.0}


def test_extract_nested_tvl_number():
    p = {"chainTvls": {"Ethereum": {"tvl": 300}}}
    assert _extract_chain_tvls(p) == {"Ethereum": 300.0}


def test_extract_nested_tvl_series_total_liquidity():
    p = {"chainTvls": {"Ethereum": {"tvl": [{"totalLiquidityUSD": 400}]}}}
    assert _extract_chain_tvls(p) == {"Ethereum": 400.0}


def test_extract_nested_tvl_series_tvl_key():
    p = {"chainTvls": {"Ethereum": {"tvl": [{"tvl": 500}]}}}
    assert _extract_chain_tvls(p) == {"Ethereum": 500.0}


def test_extract_empty_record():
    assert _extract_chain_tvls({}) == {}
