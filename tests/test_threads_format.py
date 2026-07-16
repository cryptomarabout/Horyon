"""Tests for app.threads pure tweet-composition helpers.

The thread surface is outward-facing AND irreversible (it posts to X), so the
text-shaping leash matters: em-dash de-slop, reasoning-leak stripping, handle
allow-listing (handles are DB truth, never model-coined), whole-sentence fitting
that never trails off mid-thought, and the no-bland-why rule. All pure — no DB,
LLM, or network.
"""
from __future__ import annotations

from datetime import date

from app.threads import (
    TWEET_TEXT_MAX,
    WHY_LABEL,
    _append_asset_tags,
    _build_closer,
    _clean_why,
    _clip,
    _compose_brief_tweet,
    _compose_hook,
    _dedash,
    _fallback_tweet,
    _find_surface,
    _fit,
    _fit_whole,
    _is_asset_surface,
    _masthead_date,
    _rank_line,
    _validate_handles,
)


# ── _dedash ──────────────────────────────────────────────────────────────────

def test_dedash_em_dash_to_comma():
    assert _dedash("bought at market — no VC discount") == "bought at market, no VC discount"


def test_dedash_numeric_range_to_hyphen():
    assert _dedash("raised 3—5 million") == "raised 3-5 million"


def test_dedash_collapses_whitespace():
    assert _dedash("a   b\n\tc") == "a b c"


def test_dedash_leaves_plain_hyphenated_words():
    assert _dedash("on-chain 4-year vesting") == "on-chain 4-year vesting"


def test_dedash_handles_none():
    assert _dedash(None) == ""


# ── _clip / _fit / _fit_whole ────────────────────────────────────────────────

def test_clip_returns_short_unchanged():
    assert _clip("short text", 100) == "short text"


def test_clip_word_boundary_no_ellipsis():
    out = _clip("the quick brown fox jumps over", 18)
    assert len(out) <= 18
    assert not out.endswith(" ")
    assert "…" not in out and "..." not in out


def test_clip_strips_trailing_punctuation():
    assert not _clip("alpha beta gamma—", 12).endswith(("—", "-", ",", "."))


def test_fit_keeps_whole_sentences():
    text = "First sentence here. Second sentence here. Third sentence here."
    out = _fit(text, 40)
    assert out.endswith(".")          # no dangling fragment
    assert len(out) <= 40


def test_fit_falls_back_to_clip_when_first_sentence_overflows():
    out = _fit("a" * 50, 10)
    assert len(out) <= 10


def test_fit_whole_returns_empty_when_nothing_fits():
    assert _fit_whole("a" * 50, 10) == ""


def test_fit_whole_keeps_fitting_sentence():
    assert _fit_whole("Tight take.", 50) == "Tight take."


# ── _fallback_tweet ──────────────────────────────────────────────────────────

def test_fallback_uses_analysis_over_body():
    b = {"title": "Aave V3", "analysis": "Lending TVL grew.", "body": "raw body"}
    out = _fallback_tweet(b)
    assert out.startswith("Aave V3")
    assert "Lending TVL grew" in out
    assert "raw body" not in out


def test_fallback_title_only_when_no_detail():
    assert _fallback_tweet({"title": "Just a title", "analysis": "", "body": ""}) == "Just a title"


# ── _find_surface / _is_asset_surface ────────────────────────────────────────

def test_find_surface_longest_alias_wins():
    row = {"name": "Circle", "aliases": ["USDC", "USD Coin"]}
    assert _find_surface("USD Coin supply rose", row) == "USD Coin"


def test_find_surface_preserves_original_case():
    row = {"name": "Circle", "aliases": ["USDC"]}
    assert _find_surface("usdc minted today", row) == "usdc"


def test_find_surface_ignores_at_handle_aliases():
    # @handle aliases are not matchable surfaces; only the real ticker "USDC" is.
    row = {"name": "Circle", "aliases": ["@circle", "USDC"]}
    assert _find_surface("USDC minted", row) == "USDC"


def test_find_surface_falls_back_to_name_when_absent():
    row = {"name": "Circle", "aliases": ["@circle"]}
    assert _find_surface("some unrelated headline", row) == "Circle"


def test_is_asset_surface_true_for_ticker_diff_name():
    assert _is_asset_surface("USDC", "Circle") is True


def test_is_asset_surface_false_when_matches_name():
    assert _is_asset_surface("AAVE", "Aave") is False


def test_is_asset_surface_false_for_non_ticker():
    assert _is_asset_surface("Circle", "Circle") is False


# ── _append_asset_tags ───────────────────────────────────────────────────────

def test_append_asset_tag_after_ticker():
    out = _append_asset_tags("USDC supply grew sharply", [{"surface": "USDC", "handle": "@circle"}])
    assert out == "USDC @circle supply grew sharply"


def test_append_asset_tag_skips_compound_product_name():
    # "USDC Vault" is a product name → don't split it with a handle
    out = _append_asset_tags("USDC Vault deposits rose", [{"surface": "USDC", "handle": "@circle"}])
    assert out == "USDC Vault deposits rose"


def test_append_asset_tag_noop_when_ticker_absent():
    out = _append_asset_tags("nothing relevant here", [{"surface": "USDC", "handle": "@circle"}])
    assert out == "nothing relevant here"


# ── _validate_handles ────────────────────────────────────────────────────────

def test_validate_handles_keeps_allowed_strips_unknown():
    out = _validate_handles("@circle and @fakeone shipped", {"@circle"})
    assert "@circle" in out
    assert "@fakeone" not in out
    assert "fakeone" in out          # bare word retained


def test_validate_handles_case_insensitive_allow():
    assert "@Circle" in _validate_handles("@Circle update", {"@circle"})


# ── _clean_why ───────────────────────────────────────────────────────────────

def test_clean_why_drops_bland_without_grounding():
    assert _clean_why("enhances the competitive landscape") == ""


def test_clean_why_keeps_bland_with_number():
    assert _clean_why("enhances yield by 12%") == "enhances yield by 12%"


def test_clean_why_keeps_bland_with_handle():
    assert _clean_why("could boost @aave adoption") == "could boost @aave adoption"


def test_clean_why_keeps_specific_line():
    why = "Aave now settles a third of all onchain lending."
    assert _clean_why(why) == why


def test_clean_why_empty():
    assert _clean_why("") == ""


# ── _rank_line ───────────────────────────────────────────────────────────────

def test_rank_line_shows_sources_when_two_or_more():
    assert _rank_line(1, 3) == "#1 · 3 sources"


def test_rank_line_hides_single_source():
    assert _rank_line(2, 1) == "#2"
    assert _rank_line(3, None) == "#3"
    assert _rank_line(4, 0) == "#4"


# ── _compose_brief_tweet ─────────────────────────────────────────────────────

def test_compose_brief_tweet_structure():
    out = _compose_brief_tweet(
        1, "Aave deployed on Base mainnet today.",
        "This adds $2B in lending capacity.",
        3, inline=[], assets=[], allowed=set())
    assert out.startswith("#1 · 3 sources\n")
    assert "Aave deployed on Base" in out
    assert WHY_LABEL in out
    for line in out.split("\n"):
        assert len(line) <= TWEET_TEXT_MAX


def test_compose_brief_tweet_drops_bland_why():
    out = _compose_brief_tweet(
        2, "Protocol shipped a new module.",
        "enhances the competitive landscape",      # bland, no grounding → dropped
        1, inline=[], assets=[], allowed=set())
    assert WHY_LABEL not in out
    assert out.startswith("#2\n")                   # single source → no count


# ── masthead / hook / closer ─────────────────────────────────────────────────

def test_masthead_date_format_and_zero_strip():
    out = _masthead_date(date(2026, 6, 7))     # single-digit day
    assert out == out.upper()
    assert "JUN 7, 2026" in out
    assert " 07" not in out


def test_compose_hook_has_header_and_cue():
    out = _compose_hook(date(2026, 6, 17), "Markets ripped today.", 5)
    assert "HORYON DAILY" in out
    assert "Top 5 signals" in out
    assert "Markets ripped today." in out


def test_compose_hook_zero_count_generic_cue():
    out = _compose_hook(date(2026, 6, 17), "", 0)
    assert "Today's signals" in out


def test_build_closer_has_handle_and_url():
    from app import config
    out = _build_closer(date(2026, 6, 17))
    assert "@Horyonhq" in out
    assert config.PUBLIC_BASE_URL in out
