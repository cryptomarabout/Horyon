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
    _is_soft_echo,
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


# ── _signal_corroboration (log-spaced bands, T1 recalibration 2026-07-11) ─────
# Bands on the credibility SUM: M≥3.0→25 (premium override), T≥24→25, ≥12→21,
# ≥6→17, ≥3→13, ≥1.5→9, ≥0.8→5, ≥0.4→2, else 0.

def test_corroboration_top_band_needs_broad_sum():
    # 24 Tier-2 sources (sum 24) is genuine broad corroboration → top band.
    assert _signal_corroboration([1.0] * 24) == 25


def test_corroboration_twelve_band():
    assert _signal_corroboration([1.0] * 12) == 21   # sum 12.0
    assert _signal_corroboration([1.0] * 20) == 21   # still ≥12, below the 24 top band


def test_corroboration_six_band():
    assert _signal_corroboration([1.2, 1.2, 1.2, 1.2, 1.2]) == 17   # sum 6.0


def test_corroboration_three_band():
    assert _signal_corroboration([1.2, 1.2]) == 9    # sum 2.4 → ≥1.5 band


def test_corroboration_single_trusted():
    assert _signal_corroboration([1.2]) == 5   # sum 1.2 → ≥0.8 band


def test_corroboration_single_tier2():
    assert _signal_corroboration([1.0]) == 5   # sum 1.0 → ≥0.8 band


def test_corroboration_single_clickbait():
    assert _signal_corroboration([0.4]) == 2   # sum 0.4 → ≥0.4 band


def test_corroboration_empty():
    assert _signal_corroboration([]) == 0


def test_corroboration_three_tier2_not_max():
    # 3 unknown Tier-2 sources sum to 3.0 with no trusted/premium source → 13, far
    # below max. (The cheapest republication attack cannot fake broad corroboration.)
    assert _signal_corroboration([1.0, 1.0, 1.0]) == 13


def test_corroboration_kaiko_alone_reaches_25():
    # A single Kaiko article (weight 3.0) must keep hitting the max band via the
    # premium override — the documented premium-tier property.
    assert _signal_corroboration([3.0]) == 25


def test_corroboration_kaiko_plus_others_still_max():
    assert _signal_corroboration([3.0, 1.0]) == 25


def test_corroboration_is_monotonic_in_sum():
    # Each successive band must not decrease as the credibility sum grows.
    sums = [[0.4], [1.0], [1.2, 1.2], [1.2] * 5, [1.0] * 12, [1.0] * 24]
    scores = [_signal_corroboration(c) for c in sums]
    assert scores == sorted(scores)


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


def test_criticality_phishing_theft_top_bucket():
    assert _signal_criticality("Polymarket user suffers phishing loss") == 15
    assert _signal_criticality("Funds stolen from bridge contract") == 15


def test_criticality_shuts_and_buys():
    assert _signal_criticality("Loopring shuts DEX after strategy review") == 11
    assert _signal_criticality("SBI Holdings buys Bitbank exchange") == 11


def test_criticality_ipo():
    assert _signal_criticality("Circle IPO values firm at billions") == 11


def test_criticality_debut_and_listing():
    assert _signal_criticality("Securitize NYSE debut") == 7
    assert _signal_criticality("Coinbase listing confirmed for AERO") == 7


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


def test_novelty_near_duplicate_scores_0():
    # 2 shared words covering ≥2/3 of the shorter set: slips the strict digest-gate
    # test (needs 3 shared) but the looser scoring-side check must catch it —
    # otherwise s6 is a constant (it never fired once in 60 days of production).
    covered = {"aave", "gho", "launch"}
    assert not is_semantic_duplicate({"aave", "gho", "proposal"}, covered)
    ref = _make_ref()
    ref.covered_word_sets = [covered]
    assert _signal_novelty("Aave GHO proposal", ref) == 0


def test_novelty_soft_echo_scores_2():
    # A partial thematic repeat (one shared subject, mostly new words) is neither a
    # near-duplicate nor fully fresh → the middle band. "Aave V4 hits $250M deposits"
    # softly echoes prior "Aave V4 launches new instance" without being a dup.
    covered = get_title_words("Aave V4 launches new lending instance")
    ref = _make_ref()
    ref.covered_word_sets = [covered]
    s = _signal_novelty("Aave V4 hits deposits milestone", ref)
    assert s == 2


def test_novelty_strong_dup_beats_soft_echo():
    # A near-dup covered set must win (0) even if another set is only a soft echo.
    dup = get_title_words("Aave launches on Arbitrum mainnet")
    echo = get_title_words("Aave revenue climbs this quarter")
    ref = _make_ref()
    ref.covered_word_sets = [echo, dup]
    assert _signal_novelty("Aave launches on Arbitrum", ref) == 0


# ── _is_soft_echo ────────────────────────────────────────────────────────────

def test_soft_echo_partial_overlap_true():
    assert _is_soft_echo({"aave", "v4", "deposits"}, {"aave", "v4", "launch", "instance"})


def test_soft_echo_no_overlap_false():
    assert not _is_soft_echo({"aave", "gho"}, {"bitcoin", "etf", "inflows"})


def test_soft_echo_disjoint_chains_false():
    # Same subject but different chains → not an echo (mirrors the near-dup guard).
    assert not _is_soft_echo({"uniswap", "deploys", "arbitrum"},
                             {"uniswap", "deploys", "optimism"})


def test_soft_echo_empty_false():
    assert not _is_soft_echo(set(), {"aave"})


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


def test_compute_velocity_ignores_same_source_reposts(mock_db):
    """5 reposts by ONE account within 3h is not velocity (was s3=15, now 0)."""
    now = datetime.now(timezone.utc)
    mock_db.get_feed_items_matching_terms.return_value = [
        {"link": f"https://x.com/spam/status/{i}", "ts": now - timedelta(hours=0.2 * i),
         "quality_flag": "ok", "content": "Aave update repost"}
        for i in range(5)
    ]
    out = compute_importance_scores([{"title": "Aave update", "body": ""}], "2025-01-01")
    assert out[0]["score_breakdown"]["s3"] == 0


def test_compute_velocity_counts_distinct_sources(mock_db):
    now = datetime.now(timezone.utc)
    mock_db.get_feed_items_matching_terms.return_value = [
        {"link": f"https://x.com/user{i}/status/1", "ts": now - timedelta(hours=0.2 * i),
         "quality_flag": "ok", "content": "Aave update news"}
        for i in range(5)
    ]
    out = compute_importance_scores([{"title": "Aave update", "body": ""}], "2025-01-01")
    assert out[0]["score_breakdown"]["s3"] == 15  # 5 distinct sources inside 3h


def test_compute_amount_ignores_corroborating_item_figures(mock_db):
    """An adjacent item's '$2B' must not inflate a bullet that cites no figure."""
    mock_db.get_feed_items_matching_terms.return_value = [
        {"link": "https://x.com/a/status/1", "ts": datetime.now(timezone.utc),
         "quality_flag": "ok", "content": "Aave governance vote and an unrelated $2B milestone"},
    ]
    out = compute_importance_scores([{"title": "Aave governance vote", "body": ""}], "2025-01-01")
    assert out[0]["score_breakdown"]["s1"] == 5    # one Tier-2 source (sum 1.0) DOES corroborate…
    assert out[0]["score_breakdown"]["s2"] == 0    # …but its figure is not the bullet's


def test_compute_source_count_uses_source_keys_not_domains(mock_db):
    """Two Twitter handles used to collapse into one x.com domain → badge said 1."""
    now = datetime.now(timezone.utc)
    mock_db.get_feed_items_matching_terms.return_value = [
        {"link": "https://x.com/alpha/status/1", "ts": now, "quality_flag": "ok",
         "content": "Aave update coverage"},
        {"link": "https://x.com/beta/status/2", "ts": now, "quality_flag": "ok",
         "content": "Aave update coverage"},
    ]
    out = compute_importance_scores([{"title": "Aave update", "body": ""}], "2025-01-01")
    assert out[0]["source_count"] == 2


def test_compute_uses_48h_corroboration_window(mock_db):
    compute_importance_scores([{"title": "Aave update", "body": ""}], "2025-01-01")
    _, kwargs = mock_db.get_feed_items_matching_terms.call_args
    assert kwargs.get("window_hours") == 48


def test_compute_fallback_terms_require_two_matches(mock_db):
    """Entity-less bullets corroborate via generic words (OR-matched in the DB);
    an item matching only ONE fallback term ("volume") is noise, not corroboration."""
    now = datetime.now(timezone.utc)
    mock_db.get_feed_items_matching_terms.return_value = [
        {"link": "https://x.com/noise/status/1", "ts": now,
         "quality_flag": "ok", "content": "huge volume across markets today"},
        {"link": "https://x.com/related/status/2", "ts": now,
         "quality_flag": "ok", "content": "TradeXYZ posts record volume"},
    ]
    out = compute_importance_scores(
        [{"title": "TradeXYZ hits record volume", "body": ""}], "2025-01-01")
    # Only the 2-term item survives → one Tier-2 source (sum 1.0) → s1=5, no velocity.
    assert out[0]["source_count"] == 1
    assert out[0]["score_breakdown"]["s1"] == 5
    assert out[0]["score_breakdown"]["s3"] == 0


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
        get_entity_mention_map=[("Aave", ["aave", "aav"], "protocol", 50)],
        get_protocol_tvls=[("Aave", 1.2e10), ("BadRow", None)],
    )
    ref = _RefData()
    assert "Aave" in {t for t, _ in ref.entity_terms}
    assert "aav" in {t for t, _ in ref.entity_terms}   # short ticker kept via type+mentions
    assert ("Aave", 1.2e10) in ref.protocol_tvls
    assert all(t is not None for _, t in ref.protocol_tvls)   # None-tvl row dropped


def test_refdata_rejects_generic_junk_aliases(patch_db):
    """Junk single-word aliases ("Onchain", "Public") must not become corroboration
    terms — they OR-match half the corpus and hand bullets a maxed s1/s3 on noise."""
    patch_db(
        "scoring",
        get_entity_mention_map=[
            ("Onchain", ["onchain"], "other", 1),
            ("Public", ["public"], "other", 2),
            ("Notional", ["notional"], "protocol", 2),
            ("Morpho", ["morpho"], "protocol", 40),
        ],
        get_protocol_tvls=[],
    )
    ref = _RefData()
    terms = {t.lower() for t, _ in ref.entity_terms}
    assert "onchain" not in terms
    assert "public" not in terms
    assert "notional" not in terms
    assert "morpho" in terms
