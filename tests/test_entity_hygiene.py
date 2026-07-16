"""Regression tests for the entity-junk prevention layer (2026-07-05).

Motivating incidents: LLM extraction repeatedly minted entities whose whole identity
is a common English word — `would` (protocol!), `zero`, `stake`, `block`, `cash`,
`trump-memecoin` with bare alias 'trump' — and hand-deleted junk was re-created by
the next extraction cycle within days (the audit backlog regrew 0 → 189 in 3 days).

Three layers are pinned here:
  1. extraction corpus gate  — a NEW entity whose bare name/alias is ordinary prose in
     our own feed corpus (or an English stopword) is never minted (`_common_prose_word`
     + the skip branch in extract_and_upsert_entities);
  2. runtime matcher gate    — _build_matchers routes name/slug/aliases through the
     shared matchable_term so GENERIC_TERMS / junk tokens can't word-boundary match;
  3. decision memory         — audit.check_collision_risk skips entity_review'd slugs
     so human 'keep' verdicts stop the daily re-flagging loop.
"""
from __future__ import annotations

from unittest.mock import patch

from app import entities


def _reset():
    entities._reset_matcher_cache()
    entities._PROSE_CACHE.clear()


def _row(slug, name, type_, aliases, mc=0):
    return (slug, name, type_, aliases, None, mc)


# ── layer 1: corpus prose gate ───────────────────────────────────────────────

def test_common_prose_word_stopword_is_prose():
    _reset()
    with patch("app.entities.db") as mock_db:
        mock_db.prose_doc_count.return_value = None   # tsquery dropped it → stopword
        assert entities._common_prose_word("would")


def test_common_prose_word_frequent_word_is_prose():
    _reset()
    with patch("app.entities.db") as mock_db:
        mock_db.prose_doc_count.return_value = 40     # hits the cap
        assert entities._common_prose_word("zero")


def test_common_prose_word_rare_brand_is_not_prose():
    _reset()
    with patch("app.entities.db") as mock_db:
        mock_db.prose_doc_count.return_value = 3
        assert not entities._common_prose_word("berachain")


def test_common_prose_word_skips_non_bare_tokens():
    _reset()
    with patch("app.entities.db") as mock_db:
        mock_db.prose_doc_count.return_value = None
        # multi-word / hyphenated / non-alpha identities are distinctive by construction
        assert not entities._common_prose_word("trump memecoin")
        assert not entities._common_prose_word("trump-memecoin")
        assert not entities._common_prose_word("a16z")
        mock_db.prose_doc_count.assert_not_called()


def test_common_prose_word_caches_probe():
    _reset()
    with patch("app.entities.db") as mock_db:
        mock_db.prose_doc_count.return_value = 40
        entities._common_prose_word("cash")
        entities._common_prose_word("cash")
        assert mock_db.prose_doc_count.call_count == 1


# ── layer 1b: extraction refuses to mint prose-named NEW entities ────────────

def _extract_with(mock_db, mock_llm, ents):
    mock_llm.complete.return_value = ("[]", "model")
    mock_llm.parse_json_loose.return_value = ents
    return entities.extract_and_upsert_entities(
        [{"content": "some fresh item text", "pub_date": "2026-07-05"}]
    )


def test_extraction_skips_new_common_prose_entity():
    _reset()
    with patch("app.entities.db") as mock_db, patch("app.entities.llm") as mock_llm:
        mock_db.get_entities_by_slugs.return_value = []      # slug is NEW
        mock_db.prose_doc_count.return_value = 40            # 'zero' is common prose
        n = _extract_with(mock_db, mock_llm, [
            {"slug": "zero", "name": "Zero", "type": "protocol", "aliases": []},
        ])
    assert n == 0
    mock_db.upsert_entity.assert_not_called()


def test_extraction_drops_prose_alias_keeps_distinctive_entity():
    _reset()
    with patch("app.entities.db") as mock_db, patch("app.entities.llm") as mock_llm:
        mock_db.get_entities_by_slugs.return_value = []
        # 'trump' is prose (40+ hits); hyphen/space forms are never probed
        mock_db.prose_doc_count.side_effect = lambda t, cap=40: 40 if t == "trump" else 2
        n = _extract_with(mock_db, mock_llm, [
            {"slug": "trump-memecoin", "name": "TRUMP Memecoin", "type": "other",
             "aliases": ["trump"]},
        ])
    assert n == 1
    args = mock_db.upsert_entity.call_args[0]
    assert "trump" not in args[3]                  # bare prose alias dropped
    assert "trump memecoin" in args[3]             # distinctive alias kept


def test_extraction_existing_entity_not_rejudged():
    _reset()
    with patch("app.entities.db") as mock_db, patch("app.entities.llm") as mock_llm:
        mock_db.get_entities_by_slugs.return_value = [{"slug": "solana"}]  # exists
        n = _extract_with(mock_db, mock_llm, [
            {"slug": "solana", "name": "Solana", "type": "chain", "aliases": ["sol"]},
        ])
    assert n == 1
    mock_db.prose_doc_count.assert_not_called()


# ── layer 2: runtime matcher gates name/slug/aliases ─────────────────────────

def test_matcher_rejects_generic_term_name():
    """An entity literally NAMED a GENERIC_TERMS word must not word-boundary match."""
    _reset()
    rows = [_row("hard", "hard", "protocol", ["hard"], 1)]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        assert entities.detect_entities_in_items(
            [{"content": "this was a hard decision"}]) == []
    _reset()


def test_matcher_rejects_generic_alias_on_real_entity():
    """A GENERIC_TERMS alias ('swap') on a real entity must not match; its name still does."""
    _reset()
    rows = [_row("sushiswap", "SushiSwap", "protocol", ["swap", "sushi"], 30)]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        assert entities.detect_entities_in_items(
            [{"content": "users swap tokens daily"}]) == []
        assert entities.detect_entities_in_items(
            [{"content": "SushiSwap ships a new router"}]) == ["sushiswap"]
    _reset()


def test_matcher_rejects_short_alias_on_unknown_entity():
    """A 3-char alias on a low-mention entity is not matchable (needs mc≥10 + known type)."""
    _reset()
    rows = [_row("some-fund-x", "Some Fund X", "other", ["sfx"], 1)]
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = rows
        assert entities.detect_entities_in_items(
            [{"content": "the sfx budget grew"}]) == []
    _reset()


# ── layer 3: audit decision memory ───────────────────────────────────────────

def test_collision_risk_skips_reviewed_slugs():
    from app import audit
    rows = [
        ("firm", "FiRM", "protocol", ["firm"], 1),
        ("wonderland", "Wonderland", "protocol", ["time", "wonderland"], 2),
    ]
    feed = [("a firm said the time is right " * 50,)] * 50
    with patch.object(audit, "_rows", side_effect=[rows, feed]), \
         patch.object(audit.db, "get_entity_reviews",
                      return_value={"wonderland": "keep"}):
        hits = audit.check_collision_risk()
    slugs = {h[0] for h in hits}
    assert "firm" in slugs           # unreviewed junk still flagged
    assert "wonderland" not in slugs  # human 'keep' verdict is remembered
