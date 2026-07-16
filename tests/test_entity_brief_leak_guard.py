"""Regression tests for the template-echo leak guard in app/entity_brief._clean_brief
(2026-07-07 incident): nvidia/nemotron echoed the ENTITY_BRIEF_SYSTEM output template ITSELF —
which contains a literal bullet line ("• <b>Short title</b> — What happened. Why it matters.")
— followed by its planning prose ("We need to prioritize hacks/exploits…"). The 🔎 header +
template bullet satisfied the structural "has a •" check, so 6 garbage briefs (Morpho, Jito,
Securitize…) were persisted and served on /api/search.

Pure functions only — no DB or LLM needed.
"""
from __future__ import annotations

from app.entity_brief import _clean_brief

# Trimmed verbatim from the stored Securitize brief (2026-07-07, nemotron).
_TEMPLATE_ECHO_LEAK = (
    "🔎 <b>Securitize</b>\n\n"
    "• <b>Short title</b> — What happened. Why it matters. <a href=\"url\">🔗</a>\n\n"
    "3-5 bullets.\n\n"
    "We need to prioritize hacks/exploits > launches/upgrades > governance > liquidity "
    "shifts. None are hacks. So we prioritize launches/upgrades (tokenization on Solana & "
    "Avalanche, NYSE listing). Then governance (shareholder approval)."
)

_REAL_BRIEF = (
    "🔎 <b>Aave</b>\n\n"
    "• <b>V4 deposits near $160M on Base</b> — Deposits climbed with frxUSD leading both "
    "deposits and borrows. Signals the incentive program is pulling stablecoin liquidity. "
    "<a href=\"https://x.com/aave/status/123\">🔗</a>\n"
    "• <b>GHO peg steady</b> — The stablecoin held its peg through the week's volatility. "
    "<a href=\"https://theblock.co/x\">🔗</a>"
)


def test_template_echo_rejected_whole():
    assert _clean_brief(_TEMPLATE_ECHO_LEAK) == ""


def test_planning_prose_after_header_rejected():
    leak = ("🔎 <b>Jito</b>\n\n• Let me list the recent events in order of priority.\n"
            "• We need to summarize the restaking coverage first.")
    assert _clean_brief(leak) == ""


def test_real_brief_passes_unchanged_content():
    out = _clean_brief(_REAL_BRIEF)
    assert out.startswith("🔎")
    assert "V4 deposits near $160M on Base" in out
    assert "GHO peg steady" in out


def test_think_block_stripped_then_real_brief_kept():
    out = _clean_brief("<think>planning about the task</think>\n" + _REAL_BRIEF)
    assert "planning about the task" not in out
    assert "V4 deposits near $160M on Base" in out


def test_dash_bullet_normalization_still_applies():
    out = _clean_brief("🔎 <b>Lido</b>\n\n- <b>Staking share dips</b> — Share fell to 24%. "
                       "<a href=\"https://x.com/l/1\">🔗</a>")
    assert "• <b>Staking share dips</b>" in out


def test_quoted_need_in_real_coverage_not_flagged():
    # A brief QUOTING a founder ("we need to scale") must not be rejected — the guard is
    # anchored to task-planning verbs (write/produce/prioritize/…), not the bare phrase.
    brief = ("🔎 <b>Base</b>\n\n• <b>Scaling roadmap</b> — Jesse said 'we need to scale "
             "Base tenfold' at the summit. <a href=\"https://x.com/base/9\">🔗</a>")
    out = _clean_brief(brief)
    assert "Scaling roadmap" in out
