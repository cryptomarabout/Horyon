"""Tests for app.weekly pure helpers — rotation parsing, the column-first Market
Snapshot builder, and the structural validator that gates a generated weekly report.

`_build_market_snapshot` is the data-layer source the Weekly web view reads before
falling back to parsing prose, and `validate_weekly_output` is what triggers the
single HIGH-error retry, so both deserve direct pins. No DB, LLM, or network.
"""
from __future__ import annotations

import json

from app.weekly import (
    _build_market_snapshot,
    _current_week,
    _extract_rotation,
    _strip_rotation_line,
    validate_weekly_output,
)


# ── _current_week ────────────────────────────────────────────────────────────

def test_current_week_is_monday_to_sunday():
    start, end = _current_week()
    assert start.weekday() == 0          # Monday
    assert (end - start).days == 6       # through Sunday


# ── _extract_rotation / _strip_rotation_line ─────────────────────────────────

def test_extract_rotation_valid():
    assert _extract_rotation("intro\nROTATION: ETH\nbody") == "ETH"


def test_extract_rotation_case_insensitive_value():
    assert _extract_rotation("ROTATION: alt") == "ALT"


def test_extract_rotation_invalid_falls_back_mixed():
    assert _extract_rotation("ROTATION: DOGE") == "MIXED"


def test_extract_rotation_absent_is_mixed():
    assert _extract_rotation("no rotation line here") == "MIXED"


def test_strip_rotation_line_removes_first_only():
    raw = "ROTATION: BTC\nreal content\nROTATION: ETH"
    out = _strip_rotation_line(raw)
    assert out.startswith("real content")
    assert "ROTATION: ETH" in out         # only the first is stripped


# ── _build_market_snapshot ───────────────────────────────────────────────────

def test_build_snapshot_computes_ratio_and_uppercases_symbols():
    ctx = {"market": {
        "global": {"btc_dominance": 52.1, "total_market_cap_usd": 2.5e12,
                   "market_cap_change_24h_pct": 1.2},
        "top50": [
            {"symbol": "btc", "price": 60000, "change_7d": 3.0},
            {"symbol": "eth", "price": 3000, "change_7d": -1.0},
        ],
    }}
    snap = json.loads(_build_market_snapshot(ctx))
    assert snap["btc_price"] == 60000
    assert snap["eth_7d_pct"] == -1.0
    assert abs(snap["eth_btc_ratio"] - 0.05) < 1e-9
    assert snap["btc_dominance"] == 52.1


def test_build_snapshot_drops_none_fields():
    ctx = {"market": {"top50": [{"symbol": "BTC", "price": 60000}]}}
    snap = json.loads(_build_market_snapshot(ctx))
    assert snap == {"btc_price": 60000}     # no eth → ratio/eth fields dropped
    assert "eth_btc_ratio" not in snap


def test_build_snapshot_none_when_no_data():
    assert _build_market_snapshot({}) is None
    assert _build_market_snapshot({"market": {}}) is None


# ── validate_weekly_output ───────────────────────────────────────────────────

_VALID = (
    "<b>🔥 Trending Dapps</b>\n• Uniswap volume up\n"
    "<b>📰 Key Stories</b>\n• Big story here\n"
    "<b>⚡ What To Watch</b>\nETF decision next week"
)


def test_validate_weekly_clean_is_ok():
    assert validate_weekly_output(_VALID) == []


def test_validate_weekly_empty_is_high():
    errs = validate_weekly_output("")
    assert errs and errs[0].startswith("HIGH")


def test_validate_weekly_key_stories_without_bullet_is_high():
    html = (
        "<b>🔥 Trending Dapps</b>\n• U\n"
        "<b>📰 Key Stories</b>\nno bullets in here\n"
        "<b>⚡ What To Watch</b>\nwatch this"
    )
    assert any("Key Stories" in e and e.startswith("HIGH") for e in validate_weekly_output(html))


def test_validate_weekly_missing_watch_is_high():
    html = "<b>🔥 Trending Dapps</b>\n• U\n<b>📰 Key Stories</b>\n• S\n"
    assert any("What To Watch" in e and e.startswith("HIGH") for e in validate_weekly_output(html))


def test_validate_weekly_trending_without_bullet_is_medium():
    html = (
        "<b>🔥 Trending Dapps</b>\nnothing\n"
        "<b>📰 Key Stories</b>\n• S\n"
        "<b>⚡ What To Watch</b>\nW"
    )
    errs = validate_weekly_output(html)
    assert any("Trending Dapps" in e and e.startswith("MEDIUM") for e in errs)
    assert not any(e.startswith("HIGH") for e in errs)
