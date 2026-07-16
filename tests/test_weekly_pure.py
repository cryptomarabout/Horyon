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


# ── Weekly v2 (T12): deterministic movers + rotation + section validation ────
from app.weekly import (  # noqa: E402
    build_movers_block,
    compute_rotation,
    validate_weekly_section,
    _deslop,
)
from app import prompts  # noqa: E402


def _mk_market(rows):
    """rows: [(symbol, rank, change_7d)] → ctx with a market.top50 shape."""
    return {"market": {"top50": [
        {"symbol": s, "rank": r, "change_7d": c} for s, r, c in rows]}}


def test_movers_block_is_exactly_5_plus_5():
    rows = [(f"C{i}", i, float(20 - i)) for i in range(1, 13)]  # 12 coins, +19..+8
    block = build_movers_block(_mk_market(rows))
    g = block.split("Gainers:</b>")[1].split("\n")[0]
    los = block.split("Losers:</b>")[1]
    # parse ticker+pct tokens the way the web parser does
    import re
    gain_toks = re.findall(r"\bC\d+\s[+\-]\d", g)
    lose_toks = re.findall(r"\bC\d+\s[+\-]\d", los)
    assert len(gain_toks) == 5
    assert len(lose_toks) == 5
    # gainers are the 5 highest, losers the 5 lowest
    assert "C1 +19.0%" in g       # best
    assert "C12 +8.0%" in los     # worst


def test_movers_block_none_on_no_price_data():
    assert build_movers_block({"market": {"top50": []}}) is None
    assert build_movers_block({}) is None


def test_compute_rotation_btc_led():
    ctx = _mk_market([("BTC", 1, 6.0), ("ETH", 2, 1.0)] +
                     [(f"A{i}", 10 + i, -2.0) for i in range(1, 6)])
    assert compute_rotation(ctx) == "BTC"


def test_compute_rotation_alt_led():
    ctx = _mk_market([("BTC", 1, 1.0), ("ETH", 2, 2.0)] +
                     [(f"A{i}", 10 + i, 9.0) for i in range(1, 6)])
    assert compute_rotation(ctx) == "ALT"


def test_compute_rotation_mixed_when_close():
    ctx = _mk_market([("BTC", 1, 3.0), ("ETH", 2, 2.8)] +
                     [(f"A{i}", 10 + i, 2.9) for i in range(1, 6)])
    assert compute_rotation(ctx) == "MIXED"


def test_compute_rotation_mixed_on_missing_data():
    assert compute_rotation({"market": {"top50": []}}) == "MIXED"
    # BTC present, no alts → MIXED
    assert compute_rotation(_mk_market([("BTC", 1, 5.0), ("ETH", 2, 1.0)])) == "MIXED"


def test_validate_section_flags_empty_and_dash_and_missing_bullets():
    assert any(e.startswith("HIGH") for e in validate_weekly_section("defi", ""))
    # defi requires bullets
    assert any("no bullets" in e for e in validate_weekly_section("defi", "just prose"))
    # em dash flagged
    assert any("dash" in e for e in validate_weekly_section("watch", "watch this — closely"))
    # a clean bulleted defi section passes
    assert validate_weekly_section("defi", "• Base TVL +12%, capital rotating in") == []
    # backfill sentinel is allowed without bullets
    assert validate_weekly_section(
        "defi", "Data unavailable for historical backfill") == []


def test_deslop_strips_em_dashes_keeps_numeric_range():
    assert "—" not in _deslop("bought at market — no discount")
    assert _deslop("range 3—5 today") == "range 3-5 today"


def test_section_prompts_exist_for_every_prose_section():
    for key in ("market", "defi", "trending", "stories", "watch"):
        sys = prompts.build_weekly_section_system(key)
        assert "em dash" in sys.lower()          # shared rails present
        assert prompts.WEEKLY_SECTIONS[key]["header"].startswith("<b>")
