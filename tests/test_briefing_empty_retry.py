"""Regression tests for the empty-draft retry in app/briefing._build_script (2026-07-07: the
short flash's ONLY draft was entirely reasoning-leak turns; the guard dropped them all and the
variant silently skipped — no retry, no stored row). Two holes are pinned here:

  1. an empty draft must never become the "best" draft (finish='stop' + 0 words used to read
     as complete-and-satisfying-the-floorless-break), and
  2. every variant gets fresh-draft retries when a draft yields zero usable turns.

Only llm.complete_ex is patched — the leak guard, speaker parsing, and retry loop run for real.
"""
from __future__ import annotations

from unittest.mock import patch

from app import briefing

# Planning prose under a HOST label — every turn is dropped by _is_leak_turn.
_LEAK_ONLY = ("HOST: We need to produce a script with the top stories. Must include cold open "
              "and no markdown.\n\nHOST: ... (lead story)\n\nHOST: (transition)")

_REAL_SHORT = (
    "HOST: Good morning, it's Tuesday, July 7, 2026, and this is the Horyon Flash. Here's "
    "what moved.\n\n"
    "HOST: Bonk DAO suffered a governance attack, with treasury funds moved by a hostile "
    "proposal.\n\n"
    "HOST: That's the Flash. The full briefing and feed are at Horyon dot X Y Z."
)

_SIGNALS = [{"title": "BonkDAO Governance Attack", "body": "Treasury funds moved.",
             "analysis": "", "facts": [], "entities": [], "prior": []}]


def test_empty_draft_gets_fresh_retry_on_base_prompt():
    calls: list[str] = []

    def fake_complete_ex(system, user, **kw):
        calls.append(user)
        return (_LEAK_ONLY if len(calls) == 1 else _REAL_SHORT), "model-x", "stop"

    with patch.object(briefing.llm, "complete_ex", side_effect=fake_complete_ex):
        turns, _titles, model = briefing._build_script("Tuesday, July 7, 2026", _SIGNALS, "short")

    assert len(calls) == 2
    assert calls[1] == calls[0]  # fresh retry re-sends the BASE prompt (no expand rewrite)
    assert turns and any("horyon flash" in t.lower() for _sp, t, _ch in turns)
    assert model == "model-x"


def test_good_first_draft_costs_one_call():
    with patch.object(briefing.llm, "complete_ex",
                      return_value=(_REAL_SHORT, "m", "stop")) as mocked:
        turns, _titles, _model = briefing._build_script("Tuesday, July 7, 2026", _SIGNALS, "short")
    assert mocked.call_count == 1
    assert turns


def test_all_empty_drafts_exhaust_retries_and_return_no_turns():
    with patch.object(briefing.llm, "complete_ex",
                      return_value=(_LEAK_ONLY, "m", "stop")) as mocked:
        turns, _titles, _model = briefing._build_script("Tuesday, July 7, 2026", _SIGNALS, "short")
    assert turns == []
    assert mocked.call_count == 1 + briefing._EMPTY_DRAFT_RETRIES


def test_empty_draft_never_beats_a_truncated_real_draft():
    # finish='stop' on an empty draft must not outrank a later truncated-but-real draft.
    responses = [(_LEAK_ONLY, "m", "stop"), (_REAL_SHORT, "m", "length"), (_REAL_SHORT, "m", "length")]
    with patch.object(briefing.llm, "complete_ex", side_effect=responses):
        turns, _titles, _model = briefing._build_script("Tuesday, July 7, 2026", _SIGNALS, "short")
    assert turns, "truncated real draft should be kept over the empty one"
