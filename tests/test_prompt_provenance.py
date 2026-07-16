"""Provenance-label tests for LLM-derived context re-injection.

The intelligence layer feeds its OWN earlier model output back into later prompts
(entity summaries, analyst notes, digest history). Each of those blocks must carry
an explicit "this is model-generated / unverified" label so the receiving prompt
can't launder a prior hallucination into today's output as verified background.
These tests pin the labels so a prompt refactor can't silently drop them.
"""
from __future__ import annotations

from unittest.mock import patch

from app import entities, prompts


# ── entities.build_entity_context: summary must be labeled unverified ────────

def _entity_rows():
    # (slug, name, type, aliases, summary, mention_count) — get_all_entity_aliases shape
    return [("uniswap", "Uniswap Labs", "protocol", ["uniswap", "uni"], None, 50)]


def test_entity_context_labels_summary_as_unverified():
    with patch("app.entities.db") as mock_db:
        mock_db.get_all_entity_aliases.return_value = _entity_rows()
        mock_db.get_entities_by_slugs.return_value = [
            {"slug": "uniswap", "name": "Uniswap Labs",
             "summary": "V4 hooks live on mainnet"},
        ]
        mock_db.get_protocols_by_slugs.return_value = [
            {"slug": "uniswap", "tvl_usd": 5e9, "tvl_change_7d": 2.0, "category": "DEX"},
        ]
        entities._reset_matcher_cache()
        try:
            out = entities.build_entity_context(
                [{"content": "Uniswap Labs ships a new feature"}])
        finally:
            entities._reset_matcher_cache()
    assert "state (unverified prior note): V4 hooks live on mainnet" in out
    assert "MODEL-GENERATED" in out
    assert "TVL $5.0B" in out  # verified figures still present, separately labeled


# ── build_digest_user: analyst notes / podcast / history labels ──────────────

def _digest_user(**kw):
    with patch("app.audit.prelaunch_warnings", return_value=[]):
        return prompts.build_digest_user("TEXT: something happened", "", **kw)


def test_digest_analyst_notes_use_shared_provenance_label():
    out = _digest_user(analyst_notes="• theme one")
    assert prompts.ANALYST_NOTES_LABEL in out
    assert "MODEL-GENERATED" in prompts.ANALYST_NOTES_LABEL


def test_digest_podcast_block_marked_unverified():
    out = _digest_user(podcast_context="[Bankless] Episode\n  • claim: X")
    assert "MODEL-DISTILLED" in out
    assert "unverified" in out


def test_digest_history_marked_model_written():
    out = _digest_user(digest_chain="[2026-06-30]\nsome bullet text")
    assert "MODEL-WRITTEN" in out


# ── entity brief: digest bullets are the product's own model output ──────────

def test_entity_brief_bullets_label_preserves_tense_rule():
    out = prompts.build_entity_brief_user(
        "Aave",
        [{"date": "2026-06-30", "title": "Aave update", "body": "b", "link": "https://x"}],
        [],
    )
    assert "model-written" in out
    assert "PRESERVE their tense" in out
