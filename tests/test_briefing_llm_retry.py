"""Regression tests for the LLM-failure retry in app/briefing._build_script (2026-07-15: every
morning the explainer's first chain call failed — primary model timing out on the 7200-token
draft, one fallback delisted (404), the other free-tier 429 — and the `except: break` abandoned
the variant on that single failure, so six identical self-heal attempts each gave up after one
call and the day had no Deep Dive until 10:58 UTC). Pinned here:

  1. a chain exhaustion consumes ONE attempt and the loop retries — it never aborts the variant;
  2. persistent failure still exhausts the same bounded attempt budget as empty drafts (no
     infinite loop, no extra calls);
  3. script calls carry the long-form timeout override (config.BRIEFING_LLM_TIMEOUT_SEC) — the
     global 60s ceiling is what timed the primary model out on the longest variant.

Only llm.complete_ex (and the backoff sleep) are patched — the retry loop runs for real.
"""
from __future__ import annotations

from unittest.mock import patch

from app import briefing, config

_REAL_SHORT = (
    "HOST: Good morning, it's Wednesday, July 15, 2026, and this is the Horyon Flash. Here's "
    "what moved.\n\n"
    "HOST: Bonk DAO suffered a governance attack, with treasury funds moved by a hostile "
    "proposal.\n\n"
    "HOST: That's the Flash. The full briefing and feed are at Horyon dot X Y Z."
)

_SIGNALS = [{"title": "BonkDAO Governance Attack", "body": "Treasury funds moved.",
             "analysis": "", "facts": [], "entities": [], "prior": []}]


def test_chain_failure_consumes_one_attempt_then_retries():
    calls: list[str] = []

    def fail_then_succeed(system, user, **kw):
        calls.append(user)
        if len(calls) == 1:
            raise RuntimeError("chain exhausted (429)")
        return _REAL_SHORT, "model-x", "stop"

    with patch.object(briefing.llm, "complete_ex", side_effect=fail_then_succeed), \
         patch.object(briefing.time, "sleep") as slept:
        turns, _titles, model = briefing._build_script(
            "Wednesday, July 15, 2026", _SIGNALS, "short")

    assert len(calls) == 2, "the failed call must be followed by a retry, not an abort"
    assert turns and model == "model-x"
    assert slept.called, "a failed attempt backs off before retrying (free-tier Retry-After)"


def test_persistent_chain_failure_exhausts_bounded_attempts():
    with patch.object(briefing.llm, "complete_ex",
                      side_effect=RuntimeError("chain exhausted")) as mocked, \
         patch.object(briefing.time, "sleep"):
        turns, _titles, _model = briefing._build_script(
            "Wednesday, July 15, 2026", _SIGNALS, "short")
    assert turns == []
    assert mocked.call_count == 1 + briefing._EMPTY_DRAFT_RETRIES


def test_last_attempt_failure_does_not_sleep():
    # The backoff exists to help the NEXT attempt — after the final one it would only stall.
    with patch.object(briefing.llm, "complete_ex",
                      side_effect=RuntimeError("chain exhausted")) as mocked, \
         patch.object(briefing.time, "sleep") as slept:
        briefing._build_script("Wednesday, July 15, 2026", _SIGNALS, "short")
    assert slept.call_count == mocked.call_count - 1


def test_script_calls_carry_the_long_form_timeout():
    with patch.object(briefing.llm, "complete_ex",
                      return_value=(_REAL_SHORT, "m", "stop")) as mocked:
        briefing._build_script("Wednesday, July 15, 2026", _SIGNALS, "short")
    assert mocked.call_args.kwargs.get("timeout") == config.BRIEFING_LLM_TIMEOUT_SEC
