"""Tests for app.defillama.fetch_and_store_market_data — the CoinGecko
price/mcap/FDV/supply snapshot. _cg_get (the HTTP layer) and db are mocked;
no real network or DB call.
"""
from __future__ import annotations

from unittest.mock import patch

from app.defillama import fetch_and_store_market_data


def _coin(id_, **kw):
    base = {
        "id": id_, "symbol": id_.upper(), "current_price": 1.0,
        "market_cap": 1e9, "fully_diluted_valuation": 1.2e9,
        "circulating_supply": 1e6, "total_supply": 1.2e6,
        "market_cap_rank": 10,
        "price_change_percentage_24h_in_currency": 1.0,
        "price_change_percentage_7d_in_currency": 2.0,
    }
    base.update(kw)
    return base


def test_no_coins_fetched_stores_nothing():
    with patch("app.defillama._cg_get", return_value=None), \
         patch("app.defillama.db") as mock_db:
        assert fetch_and_store_market_data(top_n=10) == 0
        mock_db.upsert_market_data.assert_not_called()


def test_stores_rows_shaped_for_db_upsert():
    with patch("app.defillama._cg_get", return_value=[_coin("aave"), _coin("uniswap")]), \
         patch("app.defillama.db") as mock_db, \
         patch("app.defillama.time.sleep"):
        n = fetch_and_store_market_data(top_n=2)
    assert n == 2
    mock_db.upsert_market_data.assert_called_once()
    (rows,), _ = mock_db.upsert_market_data.call_args
    assert {r["gecko_id"] for r in rows} == {"aave", "uniswap"}
    row = next(r for r in rows if r["gecko_id"] == "aave")
    assert row["symbol"] == "AAVE"
    assert row["price_usd"] == 1.0
    assert row["market_cap_usd"] == 1e9
    assert row["fdv_usd"] == 1.2e9
    assert row["price_change_7d_pct"] == 2.0


def test_coin_missing_id_is_skipped():
    with patch("app.defillama._cg_get", return_value=[_coin(""), _coin("aave")]), \
         patch("app.defillama.db") as mock_db, \
         patch("app.defillama.time.sleep"):
        n = fetch_and_store_market_data(top_n=2)
    assert n == 1
    (rows,), _ = mock_db.upsert_market_data.call_args
    assert len(rows) == 1 and rows[0]["gecko_id"] == "aave"


def test_duplicate_gecko_id_across_pages_is_deduped():
    # CoinGecko pages by LIVE rank; a rank shift between page requests repeats a coin on two
    # pages. Two rows with one key in a single ON CONFLICT upsert is a Postgres
    # CardinalityViolation (the recurring market-data cron crash, 2026-07-07) — the first
    # (best-rank) occurrence must win and the batch must still store.
    coins = [_coin("aave", market_cap_rank=10), _coin("uniswap"),
             _coin("aave", market_cap_rank=251, current_price=9.9)]
    with patch("app.defillama._cg_get", return_value=coins), \
         patch("app.defillama.db") as mock_db, \
         patch("app.defillama.time.sleep"):
        n = fetch_and_store_market_data(top_n=3)
    assert n == 2
    (rows,), _ = mock_db.upsert_market_data.call_args
    assert [r["gecko_id"] for r in rows] == ["aave", "uniswap"]
    kept = rows[0]
    assert kept["market_cap_rank"] == 10 and kept["price_usd"] == 1.0  # first occurrence kept
