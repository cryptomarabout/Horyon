"""Tests for the temporal-modality gate in app.audit — the SHARED core behind both the
stored-data scan and the thread/audio pre-publish fail-closed gate. A 'violation' is a
sentence that asserts a pre-launch entity is already live, with no future qualifier.

These are the highest-value tests in the suite: the modality rule is outward-facing and
irreversible (threads + audio fail closed on a violation), so a regression here is exactly
the announced-vs-deployed class of error known_facts exists to prevent.

Importing app.audit pulls in app.db; the pool is lazy (no DB needed for these pure funcs)."""
from app.audit import compile_prelaunch_patterns, modality_violations, reverse_modality_violations

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


# --------------------------------------------------------------------------- #
# reverse_modality_violations — the mirror check (2026-07-20 Robinhood Chain incident): a
# hand-verified LIVE entity described as future/planned, e.g. "a network Robinhood has signaled
# plans to expand beyond its brokerage model" about a chain live since 2026-07-01.
# --------------------------------------------------------------------------- #
from app.audit import compile_established_self_patterns  # noqa: E402

REV_PATTERNS = compile_established_self_patterns()


def test_robinhood_chain_future_framing_is_flagged():
    hits = reverse_modality_violations(
        "Robinhood Chain is a network where Robinhood plans to expand beyond its brokerage app.",
        REV_PATTERNS,
    )
    assert [slug for slug, _ in hits] == ["robinhood-chain"]


def test_robinhood_chain_live_framing_is_not_flagged():
    assert reverse_modality_violations(
        "Arcus is a self-custodial exchange live on Robinhood Chain today.", REV_PATTERNS
    ) == []


def test_base_sub_feature_future_language_is_not_flagged():
    # The false-positive this check must avoid: a live chain's own UPCOMING upgrade is still
    # correctly described in future tense, and 'beryl upgrade' is a sub-feature term, not a
    # self-reference term, so it must never trip the reverse check.
    assert reverse_modality_violations(
        "The Beryl upgrade, still in testnet, is expected to ship next quarter.", REV_PATTERNS
    ) == []


def test_base_chain_itself_future_framing_would_be_flagged():
    # Sanity check the mechanism generalizes beyond Robinhood Chain: if Base's OWN self-reference
    # term ever appeared with future language, it should trip (it never legitimately should).
    hits = reverse_modality_violations(
        "Base mainnet is expected to launch sometime next year.", REV_PATTERNS
    )
    assert [slug for slug, _ in hits] == ["base"]


def test_reverse_modality_empty_text():
    assert reverse_modality_violations("", REV_PATTERNS) == []
    assert reverse_modality_violations(None, REV_PATTERNS) == []
