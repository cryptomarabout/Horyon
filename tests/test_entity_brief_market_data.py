"""Tests for the CoinGecko market-data facts injected into entity briefs
(app.entity_brief._entity_db_facts) and the pure formatters it uses. db is mocked —
no real DB or network call.
"""
from __future__ import annotations

from unittest.mock import patch

from app.entity_brief import _entity_db_facts, _fmt_num, _fmt_price, _fmt_usd


# ── pure formatters ───────────────────────────────────────────────────────────

def test_fmt_price_two_decimals_at_or_above_a_dollar():
    assert _fmt_price(150.5) == "$150.50"
    assert _fmt_price(1.0) == "$1.00"


def test_fmt_price_significant_figures_below_a_dollar():
    # A sub-cent altcoin price rounded to 2dp would print as $0.00 — useless.
    assert _fmt_price(0.0001234) == "$0.0001234"
    assert _fmt_price(0.5) == "$0.5"


def test_fmt_num_scales_like_fmt_usd_without_the_dollar_sign():
    assert _fmt_num(14_000_000) == "14.0M"
    assert _fmt_num(1_500_000_000) == "1.50B"


# ── _entity_db_facts: market-data block ───────────────────────────────────────

def _no_tvl_no_governance(mock_db):
    mock_db.get_protocols_by_slugs.return_value = []
    mock_db.get_governance_for_entity.return_value = []


def test_market_data_facts_include_price_mcap_fdv_and_circulating_pct():
    with patch("app.entity_brief.db") as mock_db:
        _no_tvl_no_governance(mock_db)
        mock_db.get_market_data_by_slugs.return_value = [{
            "gecko_id": "aave", "symbol": "AAVE", "price_usd": 150.5,
            "market_cap_usd": 2.1e9, "fdv_usd": 2.5e9,
            "circulating_supply": 14_000_000, "total_supply": 16_000_000,
            "price_change_7d_pct": 5.2, "market_cap_rank": 45,
        }]
        out = _entity_db_facts("aave", "Aave")
    assert "CoinGecko market data" in out
    assert "price $150.50" in out
    assert "mcap $2.1B" in out
    assert "FDV $2.5B" in out
    assert "+5.2% 7d" in out
    assert "circulating (88%)" in out
    assert "AAVE" in out


def test_no_data_anywhere_returns_empty_string():
    with patch("app.entity_brief.db") as mock_db:
        _no_tvl_no_governance(mock_db)
        mock_db.get_market_data_by_slugs.return_value = []
        assert _entity_db_facts("noslug", "No Coverage Entity") == ""


def test_market_data_lookup_failure_degrades_to_no_block_not_an_exception():
    with patch("app.entity_brief.db") as mock_db:
        _no_tvl_no_governance(mock_db)
        mock_db.get_market_data_by_slugs.side_effect = Exception("db down")
        assert _entity_db_facts("aave", "Aave") == ""


def test_tvl_and_market_data_coexist_in_one_block():
    with patch("app.entity_brief.db") as mock_db:
        mock_db.get_protocols_by_slugs.return_value = [
            {"slug": "aave", "tvl_usd": 1.2e10, "tvl_change_7d": 3.0, "category": "Lending"},
        ]
        mock_db.get_governance_for_entity.return_value = []
        mock_db.get_market_data_by_slugs.return_value = [{
            "gecko_id": "aave", "symbol": "AAVE", "price_usd": 150.5,
            "market_cap_usd": 2.1e9, "fdv_usd": None,
            "circulating_supply": None, "total_supply": None,
            "price_change_7d_pct": None, "market_cap_rank": 45,
        }]
        out = _entity_db_facts("aave", "Aave")
    assert "TVL" in out and "$12.0B" in out       # existing TVL line untouched
    assert "mcap $2.1B" in out                     # new market-data line present
    assert "FDV" not in out                        # absent field omitted, not fabricated
