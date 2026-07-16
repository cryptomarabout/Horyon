"""Podcast prediction follow-through (T14) — deterministic recheck logic.

No LLM, no network, no real DB: patch_db swaps app.podcasts.db so recheck_predictions
is exercised as the pure decision it is (corroborated when later coverage exists, stale
after the window, still open otherwise).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import podcasts


def _pred(pred_id, slugs, age_days):
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {"id": pred_id, "video_id": "vid", "channel": "Bankless",
            "claim": "ETH flips $4k by Q3", "entities": slugs,
            "predicted_at": ts, "created_at": ts}


def test_corroborated_when_later_coverage_matches(patch_db):
    m = patch_db(
        "podcasts",
        get_open_predictions=[_pred(1, ["ethereum"], age_days=20)],
        get_entities_by_slugs=[{"slug": "ethereum", "name": "Ethereum"}],
        get_digest_bullets_matching_since=[
            {"digest_date": "2026-07-10", "title": "Ethereum ETF inflows accelerate"}],
    )
    with patch.object(podcasts.entities, "matchable_term", return_value=True):
        stats = podcasts.recheck_predictions()
    assert stats == {"checked": 1, "corroborated": 1, "stale": 0}
    outcome, evidence = m.update_prediction_outcome.call_args[0][1:3]
    assert outcome == "corroborated"
    assert evidence and evidence[0]["title"].startswith("Ethereum ETF")


def test_stale_when_no_coverage_past_window(patch_db):
    # Old enough to be checked AND past the stale window, but no matching coverage.
    m = patch_db(
        "podcasts",
        get_open_predictions=[_pred(2, ["someslug"], age_days=120)],
        get_entities_by_slugs=[{"slug": "someslug", "name": "SomeProject"}],
        get_digest_bullets_matching_since=[],
    )
    with patch.object(podcasts.entities, "matchable_term", return_value=True):
        stats = podcasts.recheck_predictions()
    assert stats == {"checked": 1, "corroborated": 0, "stale": 1}
    assert m.update_prediction_outcome.call_args[0][1] == "stale"


def test_stays_open_before_stale_window_without_coverage(patch_db):
    # Checked (≥ min age) but not yet stale, no coverage → left open (no update call).
    m = patch_db(
        "podcasts",
        get_open_predictions=[_pred(3, ["someslug"], age_days=20)],
        get_entities_by_slugs=[{"slug": "someslug", "name": "SomeProject"}],
        get_digest_bullets_matching_since=[],
    )
    with patch.object(podcasts.entities, "matchable_term", return_value=True):
        stats = podcasts.recheck_predictions()
    assert stats == {"checked": 1, "corroborated": 0, "stale": 0}
    m.update_prediction_outcome.assert_not_called()


def test_entity_less_prediction_goes_stale_after_window(patch_db):
    # No resolvable entities → can't match; still closes as stale after the window so the
    # open set doesn't grow forever.
    m = patch_db(
        "podcasts",
        get_open_predictions=[_pred(4, [], age_days=200)],
    )
    stats = podcasts.recheck_predictions()
    assert stats == {"checked": 1, "corroborated": 0, "stale": 1}
    assert m.update_prediction_outcome.call_args[0][1] == "stale"


def test_generic_entity_name_gated_out(patch_db):
    # matchable_term rejects the name → no terms → no match → open (not stale yet).
    m = patch_db(
        "podcasts",
        get_open_predictions=[_pred(5, ["flow"], age_days=20)],
        get_entities_by_slugs=[{"slug": "flow", "name": "flow"}],
        get_digest_bullets_matching_since=[
            {"digest_date": "2026-07-10", "title": "cash flow analysis"}],
    )
    with patch.object(podcasts.entities, "matchable_term", return_value=False):
        stats = podcasts.recheck_predictions()
    # gated out → the bullets query is never trusted → no corroboration
    assert stats["corroborated"] == 0
    m.get_digest_bullets_matching_since.assert_not_called()


def test_store_predictions_resolves_entities_and_inserts(patch_db):
    m = patch_db("podcasts", insert_podcast_predictions=2)
    ep = {"video_id": "abc", "channel": "Bankless",
          "published_at": datetime.now(timezone.utc)}
    analysis = {"predictions": ["ETH flips $4k", "Solana ETF approved by Q4"],
                "entities": ["Ethereum", "Solana"]}
    with patch.object(podcasts.entities, "detect_entities_in_text",
                      side_effect=[["ethereum"], ["solana"]]):
        podcasts._store_predictions(ep, analysis)
    rows = m.insert_podcast_predictions.call_args[0][3]
    assert [r["claim"] for r in rows] == ["ETH flips $4k", "Solana ETF approved by Q4"]
    assert rows[0]["entities"] == ["ethereum"]


def test_store_predictions_noop_without_predictions(patch_db):
    m = patch_db("podcasts", insert_podcast_predictions=0)
    podcasts._store_predictions({"video_id": "x"}, {"predictions": []})
    m.insert_podcast_predictions.assert_not_called()
