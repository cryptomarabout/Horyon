"""Tests for app.narratives deterministic helpers.

Two are load-bearing per CLAUDE.md: `_sector` (the NO-LLM, scored sector classifier
for the Research index) and `_ground_key_points` (anti-hallucination rail that drops
outward-facing key points on thin data or with figures absent from the source text).
The rest (severity, slug, source-domain, cosine) are the small pure utilities the
clustering pipeline leans on. No DB, LLM, or network.
"""
from __future__ import annotations

from app.narratives import (
    MIN_GROUNDED_FOR_KEY_POINTS,
    _cosine,
    _ground_key_points,
    _looks_like_person,
    _norm_title,
    _sector,
    _severity,
    _significant_numbers,
    _slugify,
    _source_count,
    _source_domain,
)


# ── _looks_like_person ───────────────────────────────────────────────────────

def test_looks_like_person_plain_name():
    assert _looks_like_person("Vitalik Buterin")


def test_looks_like_person_three_word_name():
    assert _looks_like_person("Sam Bankman Fried")


def test_not_person_when_narrative_keyword_present():
    assert not _looks_like_person("Stablecoin Surge")
    assert not _looks_like_person("Liquidity Rotation")


def test_not_person_single_word():
    assert not _looks_like_person("Bitcoin")


# ── _severity ────────────────────────────────────────────────────────────────

def test_severity_red_for_exploit():
    assert _severity("Protocol suffers exploit, funds drained") == "red"


def test_severity_gold_for_governance():
    assert _severity("DAO governance vote passes") == "gold"


def test_severity_green_for_launch():
    assert _severity("Aave launches on Base") == "green"


def test_severity_neutral_default():
    assert _severity("Market remained flat overnight") == "neutral"


def test_severity_red_outranks_gold():
    assert _severity("Governance upgrade hacked") == "red"


# ── _slugify / _norm_title ───────────────────────────────────────────────────

def test_slugify_basic():
    assert _slugify("Aave V3 Launch!") == "aave-v3-launch"


def test_slugify_trims_separators():
    assert _slugify("  Hello, World  ") == "hello-world"


def test_slugify_empty():
    assert _slugify("") == ""
    assert _slugify(None) == ""


def test_norm_title_strips_punct_and_lowercases():
    assert _norm_title("Aave's V3: Launch!") == "aave s v3 launch"


# ── _source_domain ───────────────────────────────────────────────────────────

def test_source_domain_strips_www():
    assert _source_domain("https://www.theblock.co/post/1") == "theblock.co"


def test_source_domain_unifies_twitter_surfaces():
    assert _source_domain("https://x.com/a") == "x.com"
    assert _source_domain("https://twitter.com/b") == "x.com"
    assert _source_domain("https://nitter.net/c") == "x.com"


def test_source_domain_empty_and_garbage():
    assert _source_domain("") == ""
    assert _source_domain("not a url") == ""


# ── _source_count ────────────────────────────────────────────────────────────

def test_source_count_distinct_domains_ignoring_nulls():
    signals = [
        {"url": "https://x.com/a"},
        {"url": "https://nitter.net/b"},     # same domain as x.com
        {"url": "https://theblock.co/c"},
        {"url": None},
    ]
    assert _source_count(signals) == 2       # {x.com, theblock.co}


def test_source_count_empty():
    assert _source_count([]) == 0


# ── _sector (deterministic scored classifier) ────────────────────────────────

def test_sector_default_when_no_signal():
    assert _sector("", "", []) == "DeFi"


def test_sector_label_keyword_wins():
    assert _sector("Stablecoin depeg risk", "", []) == "Stablecoins"
    assert _sector("Restaking momentum builds", "", []) == "Staking & Restaking"


def test_sector_category_vote_when_label_neutral():
    # No keyword signal at all → the DeFiLlama category vote decides.
    assert _sector("Q2 review", "broad recap", ["foo"], {"foo": "lending"}) == "Lending & Yield"


def test_sector_keyword_dominates_category_vote():
    # Strong label/slug keyword signal can't be hijacked by a stray category.
    out = _sector("Uniswap DEX volume", "", ["uniswap"], {"uniswap": "lending"})
    assert out == "DEX & Trading"


def test_sector_word_boundary_no_false_match():
    # "arc" is an L1 keyword but must not fire on "search" / "architecture".
    assert _sector("Search architecture overhaul", "", []) == "DeFi"


# ── _significant_numbers ─────────────────────────────────────────────────────

def test_significant_numbers_keeps_multidigit_only():
    nums = _significant_numbers("$292M raised, up 5.2x, 8% fee, 1,500 users")
    assert "292" in nums
    assert "52" in nums          # 5.2 → 52
    assert "1500" in nums        # comma stripped
    assert "8" not in nums       # single digit ignored


def test_significant_numbers_empty():
    assert _significant_numbers("") == set()
    assert _significant_numbers("no numbers here") == set()


# ── _ground_key_points ───────────────────────────────────────────────────────

def _news(title, body):
    return {"title": title, "body": body, "signal_type": "news"}


def test_ground_key_points_drops_all_on_thin_data():
    signals = [_news("t", "b"), _news("t2", "b2")]   # only 2 grounded < MIN
    assert len(signals) < MIN_GROUNDED_FOR_KEY_POINTS
    assert _ground_key_points(["Anything"], signals) == []


def test_ground_key_points_drops_ungrounded_figure():
    signals = [
        _news("TVL update", "TVL reached 292 million this week"),
        _news("Flows", "Net inflows of 150 over the period"),
        _news("Coverage", "Activity across the sector"),
    ]
    kept = _ground_key_points(
        ["TVL hit 292 million", "A fabricated 999 figure appears"], signals)
    assert "TVL hit 292 million" in kept
    assert all("999" not in k for k in kept)


def test_ground_key_points_keeps_numberless_point():
    signals = [_news("a", "body one"), _news("b", "body two"), _news("c", "body three")]
    kept = _ground_key_points(["A qualitative claim with no figures"], signals)
    assert kept == ["A qualitative claim with no figures"]


def test_ground_key_points_caps_at_three():
    signals = [_news(f"t{i}", f"body {i}") for i in range(4)]
    kept = _ground_key_points([f"point {i}" for i in range(6)], signals)
    assert len(kept) == 3


def test_ground_key_points_empty_input():
    assert _ground_key_points([], []) == []


# ── _cosine ──────────────────────────────────────────────────────────────────

def test_cosine_identical_is_one():
    assert abs(_cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_none_and_zero_vector():
    assert _cosine(None, [1.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
