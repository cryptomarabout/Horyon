"""Tests for app.known_facts — curated anti-hallucination ground truth injected into the
LLM write-paths. Locks in the slug/text matching + word-boundary behaviour so a refactor
can't silently stop a known fact from being applied."""
from app import known_facts as kf


def test_facts_for_slugs_returns_known():
    out = kf.facts_for_slugs(["arc"])
    assert out == [kf.KNOWN_FACTS["arc"]]


def test_facts_for_slugs_dedupes():
    assert kf.facts_for_slugs(["arc", "arc"]) == [kf.KNOWN_FACTS["arc"]]


def test_facts_for_slugs_unknown_is_empty():
    assert kf.facts_for_slugs(["definitely-not-a-known-entity"]) == []
    assert kf.facts_for_slugs([]) == []
    assert kf.facts_for_slugs(None) == []


def test_facts_for_text_matches_trigger_term():
    assert kf.facts_for_text("Uniswap is coming to Arc soon") == [kf.KNOWN_FACTS["arc"]]


def test_facts_for_text_is_word_boundary():
    # 'march' contains 'arc' but is not a word-boundary match -> no false trigger.
    assert kf.facts_for_text("march was a cold month") == []


def test_facts_for_text_empty_input():
    assert kf.facts_for_text("") == []
    assert kf.facts_for_text(None) == []


def test_block_empty_is_blank():
    assert kf.block([]) == ""


def test_block_renders_label_and_facts():
    out = kf.block(["fact one", "fact two"])
    assert out.startswith(kf._LABEL)
    assert "- fact one" in out
    assert "- fact two" in out
