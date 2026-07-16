"""Tests for app.entity_brief.refresh_stale_briefs — the bounded incremental top-up
that refreshes a brief between digests when an entity gets meaningful new coverage.
db + entities + _generate_brief are mocked so this exercises only the candidate-
selection logic (staleness cutoff, minimum-new-mentions threshold, max_refresh cap),
never a real DB or LLM call.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.entity_brief import refresh_stale_briefs


def _ent(name, slug=None, aliases=None):
    return {"slug": slug or name.lower(), "name": name, "type": "protocol",
            "aliases": aliases or []}


@pytest.fixture()
def mocks():
    with patch("app.entity_brief.db") as mock_db, \
         patch("app.entity_brief.entities") as mock_entities, \
         patch("app.entity_brief._generate_brief") as mock_gen:
        mock_gen.return_value = {"name": "x", "brief_html": "🔎 <b>x</b>\n\n• y", "model_used": "test"}
        yield mock_db, mock_entities, mock_gen


def _recent_items(n, content="Aave ships a new market update today"):
    return [{"content": content} for _ in range(n)]


def test_no_recent_items_short_circuits(mocks):
    mock_db, mock_entities, mock_gen = mocks
    mock_db.get_recent_feed_items.return_value = []
    assert refresh_stale_briefs() == 0
    mock_entities.detect_entities_in_items.assert_not_called()


def test_no_entities_detected_short_circuits(mocks):
    mock_db, mock_entities, mock_gen = mocks
    mock_db.get_recent_feed_items.return_value = _recent_items(3)
    mock_entities.detect_entities_in_items.return_value = []
    assert refresh_stale_briefs() == 0
    mock_db._fetchall.assert_not_called()


def test_entity_without_existing_brief_is_skipped(mocks):
    # Discovery of brand-new entities is the daily digest's job, not this one's.
    mock_db, mock_entities, mock_gen = mocks
    mock_db.get_recent_feed_items.return_value = _recent_items(3)
    mock_entities.detect_entities_in_items.return_value = ["aave"]
    mock_db._fetchall.return_value = []  # no entity_intel_brief rows at all
    assert refresh_stale_briefs() == 0
    mock_gen.assert_not_called()


def test_entity_with_fresh_brief_is_skipped(mocks):
    mock_db, mock_entities, mock_gen = mocks
    mock_db.get_recent_feed_items.return_value = _recent_items(3)
    mock_entities.detect_entities_in_items.return_value = ["aave"]
    mock_db.get_entities_by_slugs.return_value = [_ent("Aave")]
    mock_db._fetchall.return_value = [
        {"entity_name": "Aave", "updated_at": datetime.now(timezone.utc) - timedelta(minutes=30)}
    ]
    assert refresh_stale_briefs(hours=3) == 0
    mock_gen.assert_not_called()


def test_entity_below_min_new_mentions_is_skipped(mocks):
    mock_db, mock_entities, mock_gen = mocks
    # Only ONE item mentions Aave — below the default min_new_mentions=2 threshold.
    mock_db.get_recent_feed_items.return_value = (
        _recent_items(1, "Aave ships a new market") + _recent_items(2, "unrelated chatter")
    )
    mock_entities.detect_entities_in_items.return_value = ["aave"]
    mock_db.get_entities_by_slugs.return_value = [_ent("Aave")]
    mock_db._fetchall.return_value = [
        {"entity_name": "Aave", "updated_at": datetime.now(timezone.utc) - timedelta(hours=6)}
    ]
    assert refresh_stale_briefs(hours=3, min_new_mentions=2) == 0
    mock_gen.assert_not_called()


def test_stale_entity_with_enough_new_mentions_is_refreshed(mocks):
    mock_db, mock_entities, mock_gen = mocks
    mock_db.get_recent_feed_items.return_value = _recent_items(3, "Aave ships a new market")
    mock_entities.detect_entities_in_items.return_value = ["aave"]
    mock_db.get_entities_by_slugs.return_value = [_ent("Aave")]
    mock_db._fetchall.return_value = [
        {"entity_name": "Aave", "updated_at": datetime.now(timezone.utc) - timedelta(hours=6)}
    ]
    stored = refresh_stale_briefs(hours=3, min_new_mentions=2)
    assert stored == 1
    mock_gen.assert_called_once()
    mock_db.upsert_entity_intel_brief.assert_called_once()


def test_max_refresh_caps_the_batch(mocks):
    mock_db, mock_entities, mock_gen = mocks
    mock_db.get_recent_feed_items.return_value = _recent_items(3, "Aave Morpho Lido all moved today")
    mock_entities.detect_entities_in_items.return_value = ["aave", "morpho", "lido"]
    ents = [_ent("Aave"), _ent("Morpho"), _ent("Lido")]
    mock_db.get_entities_by_slugs.return_value = ents
    stale = datetime.now(timezone.utc) - timedelta(hours=6)
    mock_db._fetchall.return_value = [
        {"entity_name": e["name"], "updated_at": stale} for e in ents
    ]
    stored = refresh_stale_briefs(hours=3, min_new_mentions=2, max_refresh=2)
    assert stored == 2
    assert mock_gen.call_count == 2
