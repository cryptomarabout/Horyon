"""Regression tests for the word-floor ask-overshoot in app/briefing (2026-07-15: the explainer
shipped at 1170 words ≈ 7 minutes against an 1800-word floor — the draft was cut short, and the
floor rail could not recover it because expand passes re-asked for exactly the floor: a stated
word target is a model's ceiling of effort, so under-floor drafts plateau just short of the floor
and the loop persists the best one). Pinned here:

  1. floor variants STATE an overshot target (floor × BRIEFING_ASK_OVERSHOOT) in the first-pass
     prompt while the loop still enforces the real floor;
  2. the stated ask is capped by the variant ceiling and by the max_tokens emission budget, so
     overshooting can never cause truncation;
  3. the expand pass re-asks for the overshot target, not the bare floor;
  4. floor variants get the full BRIEFING_EXPAND_ROUNDS attempt budget, and a draft that clears
     the floor breaks out early (compliant models pay for one call only).

Only llm.complete_ex is patched — the ask computation and expand loop run for real.
"""
from __future__ import annotations

from unittest.mock import patch

from app import briefing, config

_SIGNALS = [{"title": "BonkDAO Governance Attack", "body": "Treasury funds moved.",
             "analysis": "", "facts": [], "entities": [], "prior": []}]


def _dialogue(words: int) -> str:
    """A parseable two-voice draft with ~`words` spoken words and two chapter markers (so the
    chapter-repair pass never fires and adds no extra LLM call)."""
    filler = ("The market held steady overnight. " * 400).split()
    half = max(words // 2, 1)
    return (
        "HOST: Welcome to the Horyon Deep Dive.\n"
        "## First story\n"
        f"EXPERT: {' '.join(filler[:half])}\n"
        "## Second story\n"
        f"EXPERT: {' '.join(filler[:max(words - half - 6, 1)])}\n"
    )


def _floor(variant: str) -> int:
    return briefing._variant_floor(variant)


def test_first_pass_states_overshot_target():
    floor = _floor("explainer")
    expected_ask = briefing._ask_words(config.BRIEFING_VARIANT_SPECS["explainer"], floor)
    assert expected_ask > floor, "the stated ask must overshoot the enforced floor"
    prompts_seen: list[str] = []

    def capture(system, user, **kw):
        prompts_seen.append(user)
        return _dialogue(expected_ask + 50), "model-x", "stop"

    with patch.object(briefing.llm, "complete_ex", side_effect=capture):
        briefing._build_script("Wednesday, July 15, 2026", _SIGNALS, "explainer",
                               min_words=floor)
    assert f"at least {expected_ask} words" in prompts_seen[0]


def test_ask_capped_by_token_budget():
    spec = config.BRIEFING_VARIANT_SPECS["explainer"]
    budget = int(spec["max_tokens"] / briefing._ASK_TOKENS_PER_WORD)
    assert briefing._ask_words(spec, 10_000) == budget


def test_ask_capped_by_variant_ceiling():
    spec = config.BRIEFING_VARIANT_SPECS["standard"]
    assert spec["ceiling"] > 0
    assert briefing._ask_words(spec, spec["ceiling"]) == spec["ceiling"]


def test_ask_without_floor_is_the_plain_target():
    spec = config.BRIEFING_VARIANT_SPECS["short"]
    assert briefing._ask_words(spec, 0) == spec["target_words"]


def test_expand_pass_reasks_the_overshot_target_not_the_floor():
    floor = _floor("explainer")
    ask = briefing._ask_words(config.BRIEFING_VARIANT_SPECS["explainer"], floor)
    prompts_seen: list[str] = []

    def short_then_long(system, user, **kw):
        prompts_seen.append(user)
        # first draft far under the floor, second clears it
        return (_dialogue(200) if len(prompts_seen) == 1 else _dialogue(floor + 100),
                "model-x", "stop")

    with patch.object(briefing.llm, "complete_ex", side_effect=short_then_long):
        turns, _titles, _model = briefing._build_script(
            "Wednesday, July 15, 2026", _SIGNALS, "explainer", min_words=floor)

    assert len(prompts_seen) == 2, "floor cleared on the expand pass → loop stops there"
    assert f"at least {ask} words" in prompts_seen[1]
    assert f"at least {floor} words" not in prompts_seen[1] or ask == floor
    assert briefing._turns_word_count(turns) >= floor


def test_floor_variant_gets_full_expand_budget_and_keeps_longest():
    floor = _floor("explainer")
    lengths = [300, 400, 500, 450, 480]   # never clears the floor; pass 3 is the longest

    def always_short(system, user, **kw):
        return _dialogue(lengths[min(always_short.i, len(lengths) - 1)]), "model-x", "stop"
    always_short.i = 0

    calls: list[int] = []

    def counting(system, user, **kw):
        r = always_short(system, user, **kw)
        always_short.i += 1
        calls.append(1)
        return r

    with patch.object(briefing.llm, "complete_ex", side_effect=counting):
        turns, _titles, _model = briefing._build_script(
            "Wednesday, July 15, 2026", _SIGNALS, "explainer", min_words=floor)

    expected_rounds = 1 + max(config.BRIEFING_EXPAND_ROUNDS, briefing._EMPTY_DRAFT_RETRIES)
    assert len(calls) == expected_rounds
    assert briefing._turns_word_count(turns) >= 490, "the longest under-floor draft is kept"


def test_draft_at_floor_costs_one_call():
    floor = _floor("explainer")
    with patch.object(briefing.llm, "complete_ex",
                      return_value=(_dialogue(floor + 50), "model-x", "stop")) as mocked:
        briefing._build_script("Wednesday, July 15, 2026", _SIGNALS, "explainer",
                               min_words=floor)
    assert mocked.call_count == 1
