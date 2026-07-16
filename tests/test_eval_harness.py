"""Pure deterministic checks of the scored LLM eval harness (T10/T11).

No LLM calls, no DB: exercises the check functions and the fixture invariants the
harness relies on (URLs/numbers present in fixtures, injection payload shape).
"""
from __future__ import annotations

import re

from app import eval_harness as eh
from app import known_facts


# ── URL grounding ────────────────────────────────────────────────────────────
def test_urls_grounded_accepts_cited_prompt_urls():
    corpus = "LINK: https://x.com/aave/status/12345#m and https://theblock.co/post/1/"
    out = 'See <a href="https://x.com/aave/status/12345">tweet</a> and https://theblock.co/post/1'
    ok, detail = eh.check_urls_grounded(corpus, out)
    assert ok, detail


def test_urls_grounded_flags_novel_url():
    corpus = "LINK: https://theblock.co/post/1"
    ok, detail = eh.check_urls_grounded(corpus, "source: https://evil.example/x")
    assert not ok
    assert "evil.example" in detail


def test_url_key_normalizes_tweet_fragments():
    assert eh.url_key("https://x.com/a/status/99#m") == eh.url_key(
        "https://nitter.net/a/status/99")


# ── Number grounding ─────────────────────────────────────────────────────────
def test_numbers_grounded_accepts_reformatted_figures():
    corpus = "supply grew 18% to $6.2B; reserve holds $61M"
    ok, detail = eh.check_numbers_grounded(corpus, "USDe hit $6.2B (up 18%), reserve $61M")
    assert ok, detail


def test_numbers_grounded_flags_invented_figure():
    ok, detail = eh.check_numbers_grounded("TVL is $23B", "TVL fell 37% to $14.5B")
    assert not ok
    assert "37" in detail or "145" in detail


def test_single_digit_numbers_ignored():
    ok, _ = eh.check_numbers_grounded("three AVSs affected", "across 3 AVSs, top 5 funds")
    assert ok  # 1-significant-digit figures are unverifiable noise, not hallucination


# ── Register / AI-isms ───────────────────────────────────────────────────────
def test_ai_isms_flagged():
    ok, detail = eh.check_no_ai_isms("It's important to note that we delve deeper.")
    assert not ok
    assert "important to note" in detail


def test_clean_copy_passes():
    ok, _ = eh.check_no_ai_isms("Aave's deposit base held at $23B through the cut.")
    assert ok


def test_em_dash_in_bullet_body_flagged():
    bullets = [{"title": "A", "body": "clean body"},
               {"title": "B", "body": "risky bet — no discount"}]
    ok, detail = eh.check_no_body_em_dash(bullets)
    assert not ok and "B" in detail
    ok, _ = eh.check_no_body_em_dash([bullets[0]])
    assert ok


# ── Injection checks ─────────────────────────────────────────────────────────
def test_canary_and_planted_urls():
    ok, _ = eh.check_canary_absent(f"prefix {eh.CANARY} suffix")
    assert not ok
    assert eh.check_canary_absent("normal digest text")[0]

    ok, detail = eh.check_planted_urls_absent(
        "cite https://airdrop-fast.example/claim", ("airdrop-fast.example",))
    assert not ok and "airdrop-fast" in detail
    assert eh.check_planted_urls_absent("clean", ("airdrop-fast.example",))[0]


def test_injection_cases_shape():
    # Every injection case must carry payload text; planted URLs must appear in the
    # payload itself (otherwise the absence check would be vacuous).
    for name, case in eh.INJECTION_CASES.items():
        assert case["text"], name
        for p in case["planted"]:
            assert p in case["text"], f"{name}: planted fragment {p!r} not in payload"
    assert eh.CANARY in eh.INJECTION_CASES["inj-override"]["text"]


# ── Modality (explicit patterns — no DB) ─────────────────────────────────────
def _arc_patterns():
    return {"arc": re.compile(r"(?<!\w)(?:arc|arc mainnet|arc chain)(?!\w)", re.I)}


def test_modality_check_catches_tense_upgrade():
    ok, detail = eh.check_modality("Arc mainnet is now live and operational.",
                                   _arc_patterns())
    assert not ok and "arc" in detail


def test_modality_check_allows_future_tense():
    ok, _ = eh.check_modality("Arc targets a mainnet launch later this year.",
                              _arc_patterns())
    assert ok


# ── Fixture invariants ───────────────────────────────────────────────────────
def test_fixtures_have_urls_and_figures():
    corpus = " ".join(i["content"] + " " + i["link"] for i in eh.FIXTURE_ITEMS)
    assert len(eh.url_keys(corpus)) >= 8
    from app.narratives import _significant_numbers
    assert len(_significant_numbers(corpus)) >= 6


def test_fixture_items_survive_format_items():
    from app import digest
    formatted = digest._format_items([dict(i) for i in eh.FIXTURE_ITEMS])
    assert formatted.count("TYPE:") == len(eh.FIXTURE_ITEMS)  # none dropped as too short


def test_arc_fixture_triggers_curated_known_fact():
    # The modality rail is only exercised if the Arc item actually pulls the curated
    # fact into the digest prompt (build_digest_user calls facts_for_text).
    arc_item = next(i for i in eh.FIXTURE_ITEMS if "Arc" in i["content"])
    assert known_facts.facts_for_text(arc_item["content"])
