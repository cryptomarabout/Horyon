"""Tests for app.entities pure-logic functions.

Covers matchable_term(), _fmt_usd(), _plain(), and the single-word
ambiguity detection helpers — all zero-DB pure functions.
The DB-dependent detector (detect_entities_in_items) is tested with a
mocked db so no live Postgres connection is required.
"""
from __future__ import annotations

from unittest.mock import patch

from app.entities import (
    BLOCKED_ALIASES,
    GENERIC_TERMS,
    _ambiguous_single_word,
    _cs_matchable,
    _fmt_usd,
    _plain,
    matchable_term,
)


# ── matchable_term ───────────────────────────────────────────────────────────

def test_matchable_long_term():
    assert matchable_term("Uniswap")
    assert matchable_term("Ethereum")
    assert matchable_term("protocol-v3")


def test_matchable_rejects_generic():
    for term in ("defi", "crypto", "hack", "governance", "bridge", "token"):
        assert not matchable_term(term), f"expected {term!r} to be rejected"


def test_matchable_rejects_at_handles():
    assert not matchable_term("@vitalik")
    assert not matchable_term("@coinbase")


def test_matchable_rejects_pure_digits():
    assert not matchable_term("12345")
    assert not matchable_term("0")


def test_matchable_rejects_empty():
    assert not matchable_term("")
    assert not matchable_term(None)


def test_matchable_4char_term():
    assert matchable_term("Aave")   # 4 chars, not in GENERIC_TERMS → matchable


def test_matchable_short_protocol_with_mentions():
    # 3-char protocol with ≥10 mentions and correct type
    assert matchable_term("Sui", type_="chain", mention_count=15)


def test_matchable_short_protocol_insufficient_mentions():
    # Same term but only 5 mentions → not matchable
    assert not matchable_term("Sui", type_="chain", mention_count=5)


def test_matchable_short_wrong_type():
    # 3-char with ≥10 mentions but wrong type (person) → not matchable
    assert not matchable_term("Sui", type_="person", mention_count=20)


def test_matchable_generic_terms_superset_of_blocked():
    # Every BLOCKED_ALIASES term should also be in GENERIC_TERMS
    assert BLOCKED_ALIASES.issubset(GENERIC_TERMS)


# ── _fmt_usd ─────────────────────────────────────────────────────────────────

def test_fmt_usd_trillions():
    assert _fmt_usd(2.5e12) == "$2.50T"


def test_fmt_usd_billions():
    assert _fmt_usd(3.7e9) == "$3.7B"


def test_fmt_usd_millions():
    assert _fmt_usd(150e6) == "$150M"


def test_fmt_usd_small():
    assert _fmt_usd(50_000) == "$50,000"


def test_fmt_usd_billion_boundary():
    # exactly 1B
    assert _fmt_usd(1e9) == "$1.0B"


# ── _plain ───────────────────────────────────────────────────────────────────

def test_plain_strips_html_tags():
    result = _plain("<b>Bold</b> and <i>italic</i>")
    assert "<b>" not in result
    assert "Bold" in result
    assert "italic" in result


def test_plain_collapses_whitespace():
    result = _plain("hello   \t   world")
    assert result == "hello world"


def test_plain_respects_limit():
    text = "a" * 500
    assert len(_plain(text, limit=100)) == 100


def test_plain_default_limit_300():
    text = "a" * 400
    assert len(_plain(text)) == 300


def test_plain_empty():
    assert _plain("") == ""
    assert _plain(None) == ""


# ── _ambiguous_single_word ───────────────────────────────────────────────────

def test_ambiguous_titlecase():
    assert _ambiguous_single_word("Exodus")
    assert _ambiguous_single_word("Flow")
    assert _ambiguous_single_word("Base")


def test_ambiguous_allcaps():
    assert _ambiguous_single_word("AERO")
    assert _ambiguous_single_word("LINK")


def test_not_ambiguous_multi_word():
    assert not _ambiguous_single_word("Aave Protocol")


def test_not_ambiguous_mixed_case():
    assert not _ambiguous_single_word("aave")   # lowercase → not Titlecase/ALLCAPS


def test_not_ambiguous_empty():
    assert not _ambiguous_single_word("")
    assert not _ambiguous_single_word(None)


# ── _cs_matchable ────────────────────────────────────────────────────────────

def test_cs_matchable_long_name():
    assert _cs_matchable("Exodus", "protocol", 0)   # >= 6 chars


def test_cs_matchable_titlecase_4_to_5():
    assert _cs_matchable("Aave", "protocol", 0)    # Titlecase, 4 chars


def test_cs_matchable_short_protocol_with_mentions():
    assert _cs_matchable("Sui", "chain", 10)


def test_cs_not_matchable_short_few_mentions():
    assert not _cs_matchable("Sui", "chain", 5)


def test_cs_matchable_short_lowercase_with_mentions():
    # lowercase 3-char with ≥6 mentions and matching type IS matchable
    assert _cs_matchable("btc", "protocol", 100)


# ── detect_entities_in_items (with mocked DB) ────────────────────────────────

def _make_entity_row(slug, name, type_, aliases, mention_count=0):
    return (slug, name, type_, aliases, None, mention_count)


def test_detect_basic_alias_match():
    rows = [_make_entity_row("uniswap", "Uniswap", "protocol", ["uniswap", "uni"], 50)]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        from app.entities import detect_entities_in_items
        result = detect_entities_in_items([{"content": "Uniswap launches new feature"}])
    assert "uniswap" in result


def test_detect_no_match():
    rows = [_make_entity_row("uniswap", "Uniswap", "protocol", ["uniswap"], 50)]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        from app.entities import detect_entities_in_items
        result = detect_entities_in_items([{"content": "Bitcoin price surges"}])
    assert "uniswap" not in result


def test_detect_case_sensitive_brand():
    """Titlecase brand 'Exodus' must match 'Exodus' (capital) but not 'exodus' (lower)."""
    rows = [_make_entity_row("exodus", "Exodus", "protocol", ["exodus"], 20)]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        from app.entities import detect_entities_in_items
        # Capital E → should match
        res_cap = detect_entities_in_items([{"content": "Exodus wallet update"}])
        # lowercase → should NOT match (ambiguous brand)
        res_low = detect_entities_in_items([{"content": "exodus is a term"}])
    assert "exodus" in res_cap
    assert "exodus" not in res_low


def test_detect_empty_items():
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = []
        from app.entities import detect_entities_in_items
        assert detect_entities_in_items([]) == []


def test_detect_returns_sorted():
    rows = [
        _make_entity_row("uniswap", "Uniswap", "protocol", ["uniswap"], 50),
        _make_entity_row("aave", "Aave", "protocol", ["aave"], 50),
    ]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        from app.entities import detect_entities_in_items
        result = detect_entities_in_items([{"content": "Uniswap and Aave integrate"}])
    assert result == sorted(result)


# ── matcher cache (keyed on db.entity_generation) ────────────────────────────

def test_matcher_cache_reused_until_generation_bumps():
    """entity_memory is read once and the matcher reused while the generation is stable;
    a bump (any entity write) rebuilds it."""
    from app import entities
    entities._reset_matcher_cache()
    rows = [_make_entity_row("uniswap", "Uniswap", "protocol", ["uniswap"], 50)]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        mock_db.entity_generation.return_value = 7
        entities.detect_entities_in_text("Uniswap ships v4")
        entities.detect_entities_in_text("more on Uniswap")
        assert mock_db.get_all_entity_aliases.call_count == 1   # cached across calls
        mock_db.entity_generation.return_value = 8              # an entity write bumped it
        entities.detect_entities_in_text("Uniswap again")
        assert mock_db.get_all_entity_aliases.call_count == 2   # rebuilt


def test_matcher_cache_invalidation_reflects_new_entities():
    """After a generation bump the matcher sees newly-added entities."""
    from app import entities
    entities._reset_matcher_cache()
    with patch("app.entities.db") as mock_db:
        mock_db.entity_generation.return_value = 1
        mock_db.get_all_entity_aliases.return_value = [
            _make_entity_row("uniswap", "Uniswap", "protocol", ["uniswap"], 50),
        ]
        assert entities.detect_entities_in_text("Aave and Uniswap") == ["uniswap"]
        # Aave gets upserted → generation bumps → next detect must pick it up.
        mock_db.entity_generation.return_value = 2
        mock_db.get_all_entity_aliases.return_value = [
            _make_entity_row("uniswap", "Uniswap", "protocol", ["uniswap"], 50),
            _make_entity_row("aave", "Aave", "protocol", ["aave"], 50),
        ]
        assert entities.detect_entities_in_text("Aave and Uniswap") == ["aave", "uniswap"]
