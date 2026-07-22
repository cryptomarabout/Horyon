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


# Robinhood Chain (2026-07-20 incident): a LIVE mainnet since 2026-07-01 that the model kept
# describing as something Robinhood merely "plans to" launch — the inverse of the Arc case.
def test_robinhood_chain_is_established_not_prelaunch():
    assert "robinhood-chain" in kf.ESTABLISHED_MAINNET
    assert "robinhood-chain" in kf.KNOWN_FACTS


def test_facts_for_slugs_returns_robinhood_chain():
    assert kf.facts_for_slugs(["robinhood-chain"]) == [kf.KNOWN_FACTS["robinhood-chain"]]


def test_facts_for_text_matches_robinhood_chain_variants():
    # Fires regardless of which fragmented entity name a given article used.
    for phrase in ("Arcus TVL on Robinhood Chain", "the Robinhood App Chain ecosystem",
                   "growth on Robinhood Crypto Chain"):
        assert kf.facts_for_text(phrase) == [kf.KNOWN_FACTS["robinhood-chain"]]
