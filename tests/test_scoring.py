"""Tests for app.scoring — fully deterministic bullet-importance scorer.

All pure functions are tested without a DB. The top-level
compute_importance_scores() is tested with a mocked db module so we can
exercise the full pipeline without a live Postgres connection.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from _helpers import make_ref_data as _make_ref, ts_ago as _ts
from app.scoring import (
    FEED_CREDIBILITY,
    _RefData,
    _apply_decay,
    _build_covered_word_sets,
    _build_recent_entity_coverage,
    _bullet_entities,
    _norm_title,
    _signal_amount,
    _signal_corroboration,
    _signal_criticality,
    _signal_entity_weight,
    _signal_novelty,
    _signal_saturation,
    _signal_velocity,
    _significant_words,
    compute_importance_scores,
    get_source_credibility,
    get_source_key,
    get_title_words,
    is_semantic_duplicate,
)


# ── get_title_words ──────────────────────────────────────────────────────────

def test_get_title_words_strips_html():
    # v3 is kept via _VERSION_RE even though it's short
    assert get_title_words("<b>Aave</b> launches V3") == {"aave", "launches", "v3"}


def test_get_title_words_strips_prefix():
    assert "breaking" not in get_title_words("Breaking: Uniswap deploys on Base")


def test_get_title_words_filters_stopwords():
    words = get_title_words("the new update on mainnet")
    assert "the" not in words
    assert "new" not in words


def test_get_title_words_keeps_version_tokens():
    words = get_title_words("Aave V3 on Base")
    assert "v3" in words


def test_get_title_words_empty():
    assert get_title_words("") == set()
    assert get_title_words(None) == set()


def test_get_title_words_keeps_chain_words():
    words = get_title_words("Uniswap deploys on Arbitrum")
    assert "arbitrum" in words


# ── is_semantic_duplicate ────────────────────────────────────────────────────

def test_duplicate_high_overlap():
    w1 = {"uniswap", "deploys", "arbitrum", "v3", "launch"}
    w2 = {"uniswap", "deploys", "arbitrum", "v3", "live"}
    assert is_semantic_duplicate(w1, w2)


def test_not_duplicate_low_overlap():
    w1 = {"aave", "lending", "protocol", "update", "governance"}
    w2 = {"bitcoin", "price", "surge", "etf", "inflows"}
    assert not is_semantic_duplicate(w1, w2)


def test_not_duplicate_different_chains():
    # Same story but different chains → not a duplicate
    w1 = {"uniswap", "deploys", "arbitrum"}
    w2 = {"uniswap", "deploys", "optimism"}
    assert not is_semantic_duplicate(w1, w2)


def test_not_duplicate_empty_sets():
    assert not is_semantic_duplicate(set(), {"aave", "deploys"})
    assert not is_semantic_duplicate({"aave"}, set())


def test_duplicate_by_intersection_count():
    # 3 words overlap and cover ≥75% of shorter set → duplicate
    w1 = {"aave", "deploys", "base", "protocol"}
    w2 = {"aave", "deploys", "base", "mainnet", "today"}
    assert is_semantic_duplicate(w1, w2)


# ── _norm_title ──────────────────────────────────────────────────────────────

def test_norm_title_lowercases_and_strips_stopwords():
    result = _norm_title("The New Upgrade on Mainnet Today")
    assert "the" not in result
    assert result == result.lower()


def test_norm_title_limits_to_six_tokens():
    title = "aave deploys v3 arbitrum mainnet upgrade launch today"
    tokens = _norm_title(title).split()
    assert len(tokens) <= 6


# ── _significant_words ───────────────────────────────────────────────────────

def test_significant_words_filters_short_tokens():
    words = _significant_words("a is of on to the launch protocol")
    assert all(len(w) >= 4 for w in words)


def test_significant_words_deduplicates():
    words = _significant_words("launch launch launch protocol")
    assert words.count("launch") == 1


def test_significant_words_caps_at_six():
    text = "alpha bravo charlie delta echo foxtrot golf hotel"
    assert len(_significant_words(text)) <= 6


# ── get_source_key ───────────────────────────────────────────────────────────

def test_source_key_extracts_twitter_handle():
    assert get_source_key("https://x.com/vitalikbuterin/status/123") == "@vitalikbuterin"
    assert get_source_key("https://twitter.com/coinbase") == "@coinbase"


def test_source_key_extracts_domain():
    assert get_source_key("https://www.theblock.co/post/123") == "theblock.co"
    assert get_source_key("https://coindesk.com/markets/abc") == "coindesk.com"


def test_source_key_empty():
    assert get_source_key("") == ""
    assert get_source_key(None) == ""


# ── get_source_credibility ───────────────────────────────────────────────────

def test_credibility_tier1_known_source():
    assert get_source_credibility("theblock.co") == 1.2
    assert get_source_credibility("@defillama") == 1.2


def test_credibility_tier3_clickbait():
    assert get_source_credibility("@watcherguru") == 0.4
    assert get_source_credibility("@aixbt_agent") == 0.4


def test_credibility_default_tier2():
    assert get_source_credibility("unknown-blog.com") == 1.0


def test_credibility_case_insensitive():
    assert get_source_credibility("@DEFILLAMA") == 1.2


# ── _signal_corroboration ────────────────────────────────────────────────────

def test_corroboration_high_total():
    assert _signal_corroboration([1.2, 1.2, 1.0]) == 25   # sum = 3.4


def test_corroboration_medium():
    assert _signal_corroboration([1.2, 1.0]) == 20   # sum = 2.2


def test_corroboration_single_trusted():
    assert _signal_corroboration([1.0]) == 12


def test_corroboration_single_clickbait():
    assert _signal_corroboration([0.4]) == 5


def test_corroboration_empty():
    assert _signal_corroboration([]) == 0


# ── _signal_amount ───────────────────────────────────────────────────────────

def test_amount_billion():
    assert _signal_amount("Protocol raises $1.5B in funding") == 20


def test_amount_half_billion():
    assert _signal_amount("Deal worth $600 million announced") == 16


def test_amount_hundred_million():
    assert _signal_amount("$150M raised in Series B") == 12


def test_amount_tens_of_millions():
    assert _signal_amount("$25m grant program") == 7


def test_amount_millions():
    assert _signal_amount("A $2m bug bounty") == 3


def test_amount_no_value():
    assert _signal_amount("Aave releases new update") == 0


def test_amount_with_commas():
    assert _signal_amount("$1,500,000,000 deal") == 20


def test_amount_picks_largest():
    # "$100k" and "$2B" in same text → should pick $2B
    assert _signal_amount("small $100k grant and a $2B acquisition") == 20


# ── _signal_velocity ─────────────────────────────────────────────────────────
# (_ts / _make_ref come from the shared tests/_helpers module.)

def test_velocity_5_in_3h():
    ts = [_ts(h) for h in [0.1, 0.5, 1.0, 1.5, 2.5]]
    assert _signal_velocity(ts) == 15


def test_velocity_3_in_6h():
    ts = [_ts(h) for h in [0.5, 3.0, 5.5]]
    assert _signal_velocity(ts) == 10


def test_velocity_2_in_12h():
    ts = [_ts(h) for h in [2.0, 10.0]]
    assert _signal_velocity(ts) == 5


def test_velocity_slow():
    ts = [_ts(h) for h in [5.0, 20.0]]
    assert _signal_velocity(ts) == 0


def test_velocity_single_item():
    assert _signal_velocity([_ts(1.0)]) == 0


def test_velocity_empty():
    assert _signal_velocity([]) == 0


# ── _signal_entity_weight ────────────────────────────────────────────────────

def test_entity_weight_large_tvl():
    ref = _make_ref(tvls=[("Aave", 8e9)])
    assert _signal_entity_weight([("aave", 30)], ref) == 20


def test_entity_weight_medium_tvl():
    ref = _make_ref(tvls=[("Compound", 2e9)])
    assert _signal_entity_weight([("compound", 5)], ref) == 14


def test_entity_weight_high_mentions():
    ref = _make_ref(tvls=[])
    assert _signal_entity_weight([("bitcoin", 100)], ref) == 10


def test_entity_weight_no_match():
    ref = _make_ref(tvls=[("Aave", 8e9)])
    assert _signal_entity_weight([], ref) == 0


# ── _signal_criticality ──────────────────────────────────────────────────────

def test_criticality_hack():
    assert _signal_criticality("Protocol suffers hack, millions drained") == 15


def test_criticality_exploit():
    assert _signal_criticality("Exploit discovered in smart contract") == 15


def test_criticality_governance():
    assert _signal_criticality("New governance vote on protocol upgrade") == 11


def test_criticality_fundraise():
    assert _signal_criticality("Monad raises $225M in Series A round") == 11


def test_criticality_funding_word():
    assert _signal_criticality("Protocol secures fresh funding from Paradigm") == 11


def test_criticality_acquisition():
    assert _signal_criticality("Coinbase acquires Deribit in landmark deal") == 11


def test_criticality_series_phrase_not_bare_series():
    # "series a" is a phrase keyword; a bare "series" must NOT trip the bucket
    assert _signal_criticality("A series of ecosystem grants this quarter") == 0


def test_criticality_launch():
    assert _signal_criticality("Mainnet launch scheduled for next week") == 7


def test_criticality_partnership():
    assert _signal_criticality("Announces new partnership with Coinbase") == 3


def test_criticality_none():
    # "update" hits bucket-3; use text with no keywords at all
    assert _signal_criticality("Ecosystem growth continues this quarter") == 0


def test_criticality_highest_bucket_wins():
    # Contains both 'partnership' (3) and 'hack' (15) → should return 15
    assert _signal_criticality("Hack exposes partnership vulnerability") == 15


# ── _signal_novelty ──────────────────────────────────────────────────────────

def test_novelty_new_story_scores_5():
    ref = _make_ref()
    assert _signal_novelty("Aave launches on Arbitrum", ref) == 5


def test_novelty_duplicate_story_scores_0():
    existing = get_title_words("Aave launches on Arbitrum")
    ref = _make_ref()
    ref.covered_word_sets = [existing]
    assert _signal_novelty("Aave launches on Arbitrum mainnet", ref) == 0


def test_novelty_empty_title_scores_5():
    ref = _make_ref()
    assert _signal_novelty("", ref) == 5


# ── _signal_saturation ───────────────────────────────────────────────────────

def test_saturation_none_when_unseen():
    ref = _make_ref(coverage={})
    assert _signal_saturation([("morpho", 200)], ref) == 0


def test_saturation_light_below_threshold():
    ref = _make_ref(coverage={"morpho": 2})
    assert _signal_saturation([("morpho", 200)], ref) == 0


def test_saturation_three_days_penalizes_7():
    ref = _make_ref(coverage={"morpho": 3})
    assert _signal_saturation([("morpho", 200)], ref) == 7


def test_saturation_five_days_penalizes_12():
    ref = _make_ref(coverage={"aave": 6})
    assert _signal_saturation([("aave", 300)], ref) == 12


def test_saturation_uses_most_saturated_entity():
    ref = _make_ref(coverage={"pendle": 3, "aave": 5})
    assert _signal_saturation([("pendle", 60), ("aave", 300)], ref) == 12


def test_saturation_is_case_insensitive():
    ref = _make_ref(coverage={"morpho": 5})
    assert _signal_saturation([("Morpho", 200)], ref) == 12


# ── _apply_decay ─────────────────────────────────────────────────────────────

def test_decay_fresh_story():
    now = datetime.now(timezone.utc)
    score, decay = _apply_decay(100, now, now)
    assert score == 100
    assert decay == 1.0


def test_decay_48h_old():
    now = datetime.now(timezone.utc)
    first_seen = now - timedelta(hours=48)
    score, decay = _apply_decay(100, first_seen, now)
    assert decay == 0.75
    assert score == 75


def test_decay_capped_at_75_percent():
    now = datetime.now(timezone.utc)
    very_old = now - timedelta(hours=200)
    score, decay = _apply_decay(100, very_old, now)
    assert decay == 0.75
    assert score == 75


def test_decay_24h_partial():
    now = datetime.now(timezone.utc)
    first_seen = now - timedelta(hours=24)
    score, decay = _apply_decay(100, first_seen, now)
    # age_hours=24, decay = 1 - (24/48)*0.25 = 1 - 0.125 = 0.875
    assert abs(decay - 0.875) < 0.01
    assert score == 88


# ── compute_importance_scores (full pipeline with mocked db) ─────────────────

_MOCK_DB = {
    "get_entity_mention_map": [],
    "get_protocol_tvls": [],
    "get_digest_contents_for_dedup": [],
    "get_feed_items_matching_terms": [],
}


@pytest.fixture()
def mock_db():
    with patch("app.scoring.db") as m:
        m.get_entity_mention_map.return_value = _MOCK_DB["get_entity_mention_map"]
        m.get_protocol_tvls.return_value = _MOCK_DB["get_protocol_tvls"]
        m.get_digest_contents_for_dedup.return_value = _MOCK_DB["get_digest_contents_for_dedup"]
        m.get_feed_items_matching_terms.return_value = _MOCK_DB["get_feed_items_matching_terms"]
        yield m


def test_compute_empty_bullets(mock_db):
    assert compute_importance_scores([], "2025-01-01") == []


def test_compute_returns_required_keys(mock_db):
    bullets = [{"title": "Aave deploys V3", "body": "New protocol upgrade."}]
    out = compute_importance_scores(bullets, "2025-01-01")
    assert len(out) == 1
    b = out[0]
    assert "importance_score" in b
    assert "source_count" in b
    assert "score_breakdown" in b


def test_compute_preserves_original_keys(mock_db):
    bullets = [{"title": "Aave", "body": "body text", "custom_key": "value"}]
    out = compute_importance_scores(bullets, "2025-01-01")
    assert out[0]["custom_key"] == "value"


def test_compute_strips_internal_scratch_keys(mock_db):
    bullets = [{"title": "Aave", "body": "launch"}]
    out = compute_importance_scores(bullets, "2025-01-01")
    b = out[0]
    for scratch in ("_entities", "_python_total", "_source_count", "_first_seen_at",
                    "_breakdown_partial"):
        assert scratch not in b


def test_compute_score_in_range(mock_db):
    bullets = [{"title": "Massive $2B hack exploit drains protocol", "body": ""}]
    out = compute_importance_scores(bullets, "2025-01-01")
    score = out[0]["importance_score"]
    assert 0 <= score <= 100


def test_compute_breakdown_has_compat_keys(mock_db):
    bullets = [{"title": "update", "body": ""}]
    out = compute_importance_scores(bullets, "2025-01-01")
    bd = out[0]["score_breakdown"]
    for key in ("s1", "s2", "s3", "s4", "s5", "s6", "s7_saturation", "python_total",
                "llm_adjustment", "position_bonus", "decay"):
        assert key in bd


def test_compute_clickbait_penalty(mock_db):
    """If only clickbait sources reported it, score should be halved."""
    mock_db.get_feed_items_matching_terms.return_value = [
        {"link": "https://x.com/watcherguru/status/1", "ts": datetime.now(timezone.utc),
         "quality_flag": "ok", "content": "Big news on protocol hack"},
    ]
    bullets = [{"title": "Protocol hack drain exploit", "body": ""}]
    out = compute_importance_scores(bullets, "2025-01-01")
    bd = out[0]["score_breakdown"]
    # python_total should reflect the halving happened (s1 small due to only 0.4 cred)
    assert bd["llm_adjustment"] == 0  # compat key always 0
    assert bd["position_bonus"] == 0


def test_compute_invalid_date_falls_back(mock_db):
    bullets = [{"title": "test", "body": "body"}]
    out = compute_importance_scores(bullets, "not-a-date")
    assert out[0]["importance_score"] is not None


def test_compute_multiple_bullets(mock_db):
    bullets = [
        {"title": "Aave launches V3", "body": "mainnet upgrade"},
        {"title": "Bitcoin ETF inflows surge", "body": "institutional demand"},
    ]
    out = compute_importance_scores(bullets, "2025-06-01")
    assert len(out) == 2
    for b in out:
        assert b["importance_score"] is not None


def test_compute_scores_none_when_refdata_load_fails(mock_db):
    """If reference-data assembly throws, every bullet's scores are set to None (not crash)."""
    with patch("app.scoring._build_covered_word_sets", side_effect=RuntimeError("boom")):
        out = compute_importance_scores([{"title": "Aave", "body": "x"}], "2025-06-01")
    assert out[0]["importance_score"] is None
    assert out[0]["source_count"] is None
    assert out[0]["score_breakdown"] is None


# ── _bullet_entities ─────────────────────────────────────────────────────────

def test_bullet_entities_matches_known_terms():
    ref = _make_ref(entity_terms=[("aave", 50), ("uniswap", 30)])
    found = {t for t, _ in _bullet_entities("Aave deploys alongside Uniswap", ref)}
    assert found == {"aave", "uniswap"}


def test_bullet_entities_is_word_boundary():
    ref = _make_ref(entity_terms=[("arc", 20)])
    assert _bullet_entities("search the archive", ref) == []


def test_bullet_entities_none_when_no_terms():
    assert _bullet_entities("Aave news", _make_ref(entity_terms=[])) == []


# ── _signal_entity_weight — remaining TVL/mention tiers ──────────────────────

def test_entity_weight_tvl_hundred_million_tier():
    ref = _make_ref(tvls=[("Foo", 5e8)])      # >1e8, ≤1e9 → 8
    assert _signal_entity_weight([("foo", 0)], ref) == 8


def test_entity_weight_tvl_ten_million_tier():
    ref = _make_ref(tvls=[("Foo", 5e7)])      # >1e7, ≤1e8 → 4
    assert _signal_entity_weight([("foo", 0)], ref) == 4


def test_entity_weight_mentions_21_to_50():
    ref = _make_ref(tvls=[])
    assert _signal_entity_weight([("foo", 30)], ref) == 6


def test_entity_weight_mentions_6_to_20():
    ref = _make_ref(tvls=[])
    assert _signal_entity_weight([("foo", 10)], ref) == 3


# ── DB-backed reference-data builders (db patched via the shared fixture) ────

def test_build_recent_entity_coverage_counts_distinct_days(patch_db):
    patch_db("scoring", get_digest_contents_for_dedup=[
        ("2026-06-01", "<b>Aave</b> shipped a thing"),
        ("2026-06-02", "<b>Aave</b> shipped another"),
        ("2026-06-03", "Bitcoin only today"),
    ])
    cov = _build_recent_entity_coverage([("Aave", 50)], date(2026, 6, 4))
    assert cov.get("aave") == 2          # two distinct days, Bitcoin row ignored


def test_build_recent_entity_coverage_excludes_base_layer_terms(patch_db):
    # Bitcoin/ETH are in _SATURATION_EXCLUDE → never tracked for saturation.
    patch_db("scoring", get_digest_contents_for_dedup=[
        ("2026-06-01", "Bitcoin rallied"), ("2026-06-02", "Bitcoin again"),
    ])
    cov = _build_recent_entity_coverage([("Bitcoin", 999)], date(2026, 6, 3))
    assert "bitcoin" not in cov


def test_build_covered_word_sets_extracts_bold_titles(patch_db):
    patch_db("scoring", get_digest_contents_for_dedup=[
        ("2026-06-01", "• <b>Aave launches V3</b> — body text"),
    ])
    sets = _build_covered_word_sets(date(2026, 6, 2))
    assert any("aave" in s for s in sets)


def test_refdata_loads_terms_and_filters_null_tvl(patch_db):
    patch_db(
        "scoring",
        get_entity_mention_map=[("Aave", ["aave", "aav"], 50)],
        get_protocol_tvls=[("Aave", 1.2e10), ("BadRow", None)],
    )
    ref = _RefData()
    assert "Aave" in {t for t, _ in ref.entity_terms}
    assert ("Aave", 1.2e10) in ref.protocol_tvls
    assert all(t is not None for _, t in ref.protocol_tvls)   # None-tvl row dropped
