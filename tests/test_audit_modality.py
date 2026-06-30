"""Tests for the temporal-modality gate in app.audit — the SHARED core behind both the
stored-data scan and the thread/audio pre-publish fail-closed gate. A 'violation' is a
sentence that asserts a pre-launch entity is already live, with no future qualifier.

These are the highest-value tests in the suite: the modality rule is outward-facing and
irreversible (threads + audio fail closed on a violation), so a regression here is exactly
the announced-vs-deployed class of error known_facts exists to prevent.

Importing app.audit pulls in app.db; the pool is lazy (no DB needed for these pure funcs)."""
from app.audit import compile_prelaunch_patterns, modality_violations

# A minimal explicit pre-launch set so the test doesn't depend on live entity_memory.
PATTERNS = compile_prelaunch_patterns({"arc": ["arc", "circle's arc"]})


def test_compiled_pattern_is_word_boundary():
    pat = PATTERNS["arc"]
    assert pat.search("deploying on Arc")
    assert pat.search("ARC mainnet")
    assert not pat.search("arcade game")   # not a word boundary
    assert not pat.search("march madness")


def test_live_assertion_is_flagged():
    hits = modality_violations("Uniswap deployed on Arc.", PATTERNS)
    assert [slug for slug, _ in hits] == ["arc"]


def test_future_qualifier_is_not_flagged():
    # 'will' makes it an announcement, not a contradiction.
    assert modality_violations("Uniswap will deploy on Arc.", PATTERNS) == []


def test_testnet_qualifier_is_not_flagged():
    # The motivating safety case: testnet framing must NOT be read as a live deployment.
    assert modality_violations("Arc testnet is live for developers.", PATTERNS) == []


def test_no_prelaunch_entity_no_violation():
    # Live assertion, but about an entity that isn't in the pre-launch set.
    assert modality_violations("Uniswap deployed on Base.", PATTERNS) == []


def test_sentence_scoped_not_cross_sentence():
    # Live assertion and the entity are in DIFFERENT sentences -> not a violation.
    text = "Arc was announced in 2026. Uniswap deployed on Base."
    assert modality_violations(text, PATTERNS) == []


def test_empty_text():
    assert modality_violations("", PATTERNS) == []
    assert modality_violations(None, PATTERNS) == []
