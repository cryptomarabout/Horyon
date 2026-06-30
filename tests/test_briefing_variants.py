"""Tests for the THREE audio-briefing length variants (short / standard / explainer).

The whole point of the multi-length feature is that all three shows render from ONE set of
grounded signals under the SAME anti-hallucination rails — only verbosity/structure differ. These
tests pin that invariant: a future edit that makes the flash or the deep dive diverge on grounding,
temporal-modality, or the per-signal VERIFIED-FACT payload should fail here.

Pure functions only (prompt builders + config specs) — no DB, LLM, or TTS needed."""
from app import config, prompts

H, E = "Maya", "Daniel"
VARIANTS = ("short", "standard", "explainer")
SYS = {v: prompts.build_briefing_system(H, E, v) for v in VARIANTS}


def test_grounding_rails_identical_across_variants():
    # The grounding + write-for-the-ear blocks must appear VERBATIM in every variant's system
    # prompt — a shorter/longer show never relaxes the factual leash.
    for v in VARIANTS:
        assert prompts._BRIEFING_GROUNDING in SYS[v], f"{v}: grounding block missing/changed"
        assert prompts._BRIEFING_EAR_RULES in SYS[v], f"{v}: ear-rules block missing/changed"
        assert "PRESERVE TEMPORAL MODALITY" in SYS[v]
        assert "VERIFIED FACT" in SYS[v]


def test_unknown_variant_falls_back_to_standard():
    assert prompts.build_briefing_system(H, E, "bogus") == SYS["standard"]


def test_short_is_single_voice_no_chapters():
    s = SYS["short"]
    assert "SINGLE anchor" in s and "do NOT write an 'EXPERT'" in s
    assert "CHAPTER MARKERS" not in s              # the flash has no chapters
    assert "CEILING" in s                          # length is a cap, not a floor


def test_standard_and_explainer_are_two_voice_with_chapters():
    for v in ("standard", "explainer"):
        assert "EXPERT" in SYS[v] and "CHAPTER MARKERS" in SYS[v]


def test_explainer_guards_against_padding_with_invention():
    # The long form is the highest fabrication risk; depth must come from explanation, not new facts.
    assert "DEPTH MUST COME FROM EXPLANATION, NOT INVENTION" in SYS["explainer"]
    assert "ten-to-twelve-minute" in SYS["explainer"]


def test_user_prompt_signal_payload_identical_across_variants():
    sig = [{
        "title": "Aave V4 incentives", "body": "body text", "analysis": "the take",
        "facts": ["Aave V4 is announced, not deployed."],
        "entities": ["Aave (protocol)"], "prior": ["Jun 1: earlier note"],
    }]
    users = {v: prompts.build_briefing_user("Monday, June 21, 2026", 500, sig, H, E, v)
             for v in VARIANTS}
    # The grounded per-signal lines (the VERIFIED FACT, the player intro, prior coverage) must reach
    # the model regardless of which length is being rendered.
    for v in VARIANTS:
        assert "VERIFIED FACT (overrides the above, obey it): Aave V4 is announced" in users[v]
        assert "About the players" in users[v]
        assert "Earlier coverage" in users[v]
    # Short tells the model NOT to write chapter markers; the longer forms ask for them.
    assert "Do NOT write chapter markers" in users["short"]
    assert "chapter marker before each story" in users["standard"]
    assert "chapter marker before each story" in users["explainer"]


def test_variant_specs_are_consistent():
    specs = config.BRIEFING_VARIANT_SPECS
    assert set(config.BRIEFING_VARIANTS) == set(specs)
    # Ordered shortest → longest by both word target and bullet count.
    assert specs["short"]["target_words"] < specs["standard"]["target_words"] < specs["explainer"]["target_words"]
    assert specs["short"]["max_bullets"] <= specs["standard"]["max_bullets"] <= specs["explainer"]["max_bullets"]
    # The deep dive needs MORE raw material than the standard show, or it has nothing extra to go
    # deep on and converges to the same length — the bug this guards against.
    assert specs["explainer"]["max_bullets"] > specs["standard"]["max_bullets"]
    # The flash is single-voice; the podcast + deep dive are two-voice.
    assert specs["short"]["dialogue"] is False
    assert specs["standard"]["dialogue"] is True and specs["explainer"]["dialogue"] is True
    # Default variant is one we actually build.
    assert config.BRIEFING_DEFAULT_VARIANT in specs


def test_word_floor_orders_variants_and_only_floors_the_long_shows():
    from app import briefing
    # The flash is a tight CEILING, never a floor.
    assert briefing._variant_floor("short") == 0
    # Standard + explainer get a floor at ~target_words; explainer's is the higher.
    std_floor = briefing._variant_floor("standard")
    exp_floor = briefing._variant_floor("explainer")
    assert std_floor > 0 and exp_floor > std_floor
    # The cross-variant guard: the explainer floor is raised so it clears the standard show's ACTUAL
    # length × the over-ratio — this is what stops the deep dive landing at/below the briefing.
    big_standard = 1800
    raised = briefing._variant_floor("explainer", int(big_standard * config.BRIEFING_EXPLAINER_OVER_STANDARD))
    assert raised >= int(big_standard * config.BRIEFING_EXPLAINER_OVER_STANDARD) > big_standard


def test_expand_prompt_keeps_grounding_and_pushes_length():
    sig = [{
        "title": "Aave V4 incentives", "body": "body text", "analysis": "the take",
        "facts": ["Aave V4 is announced, not deployed."],
        "entities": ["Aave (protocol)"], "prior": ["Jun 1: earlier note"],
    }]
    p = prompts.build_briefing_expand_user(
        "Monday, June 22, 2026", "HOST: a tiny draft\nEXPERT: too short", 40, 1800, sig, H, E)
    # Re-supplies the grounded payload so extra length comes from explanation, not invention.
    assert "VERIFIED FACT (overrides the above, obey it): Aave V4 is announced" in p
    assert "NOT INVENTION" in p
    # States the concrete floor it must clear and feeds the prior draft back in.
    assert "1800 words" in p and "a tiny draft" in p
