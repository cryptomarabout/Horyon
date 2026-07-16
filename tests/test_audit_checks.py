"""Tests for the non-modality parts of app.audit — the hype/overstatement scan, the
generic pre-launch warning generator, and the HTML helpers that feed every check.

Complements test_audit_modality.py (which pins the temporal-modality core). DB-backed
entry points are kept out of scope by patching the module's `prelaunch_entities`; the
checks themselves take surfaces/text directly, so they stay pure.
"""
from __future__ import annotations

from app import audit
from app import known_facts as kf


# ── _strip / _bullets ────────────────────────────────────────────────────────

def test_strip_removes_tags_and_collapses_ws():
    assert audit._strip("<b>Hi</b>   there\n\nfriend") == "Hi there friend"


def test_strip_none():
    assert audit._strip(None) == ""


def test_bullets_extracts_only_bullet_lines():
    html = "preamble\n• <b>One</b> story\n• <b>Two</b> story\nfooter"
    assert audit._bullets(html) == ["• One story", "• Two story"]


def test_bullets_empty():
    assert audit._bullets("") == []
    assert audit._bullets("no bullets here") == []


# ── check_overstatement (_HYPE_RE) ───────────────────────────────────────────

def test_overstatement_flags_hype_phrases():
    surfaces = [("thread", "k", "This token will dominate and skyrocket to the moon")]
    out = audit.check_overstatement(surfaces)
    assert out
    assert all(f["kind"] == "thread" for f in out)
    phrases = " ".join(f["phrase"].lower() for f in out)
    assert "skyrocket" in phrases or "to the moon" in phrases or "will dominate" in phrases


def test_overstatement_skips_narratives():
    # narratives intentionally carry a contrarian/thesis voice → exempt.
    surfaces = [("narrative", "k", "This will dominate everything, guaranteed")]
    assert audit.check_overstatement(surfaces) == []


def test_overstatement_clean_text_passes():
    surfaces = [("digest", "k", "Aave deployed a new lending module on Base.")]
    assert audit.check_overstatement(surfaces) == []


def test_overstatement_includes_context_snippet():
    surfaces = [("thread", "k", "The whole market is unstoppable this cycle")]
    out = audit.check_overstatement(surfaces)
    assert out and "unstoppable" in out[0]["ctx"]


# ── check_reasoning_leak (_REASONING_LEAK_RE) ────────────────────────────────

def test_reasoning_leak_catches_brief_template_echo():
    # 2026-07-07 incident shape: the entity-brief prompt's own format template echoed back
    # ("Short title — What happened. Why it matters.") + "we"-voice planning. The old net
    # missed all 6 contaminated briefs.
    surfaces = [("brief", "Securitize",
                 "Short title — What happened. Why it matters. We need to prioritize "
                 "hacks/exploits over launches. Then governance.")]
    out = audit.check_reasoning_leak(surfaces)
    assert out and out[0]["kind"] == "brief" and out[0]["count"] >= 2


def test_reasoning_leak_we_voice_planning_verbs():
    surfaces = [("brief", "Jito", "We need to summarize the restaking coverage first.")]
    assert audit.check_reasoning_leak(surfaces)


def test_reasoning_leak_clean_brief_passes():
    surfaces = [("brief", "Aave",
                 "Aave V4 deposits on Base neared $160M, led by frxUSD. Watch the incentive "
                 "program's next epoch. Jesse said 'we need to scale Base tenfold'.")]
    assert audit.check_reasoning_leak(surfaces) == []


# ── prelaunch_warnings (prelaunch_entities patched) ──────────────────────────

def test_prelaunch_warnings_flags_uncurated_entity(monkeypatch):
    monkeypatch.setattr(audit, "prelaunch_entities", lambda: {"zkfoo": ["zkfoo", "zk foo"]})
    out = audit.prelaunch_warnings("zkfoo is live now", exclude_curated=True)
    assert len(out) == 1
    assert "PRESERVE TEMPORAL MODALITY" in out[0]
    assert "zkfoo" in out[0]


def test_prelaunch_warnings_no_match(monkeypatch):
    monkeypatch.setattr(audit, "prelaunch_entities", lambda: {"zkfoo": ["zkfoo"]})
    assert audit.prelaunch_warnings("nothing relevant here") == []


def test_prelaunch_warnings_excludes_curated_by_default(monkeypatch):
    curated = next(iter(kf.KNOWN_FACTS))
    monkeypatch.setattr(audit, "prelaunch_entities", lambda: {curated: [curated]})
    text = f"context {curated} context"
    assert audit.prelaunch_warnings(text, exclude_curated=True) == []
    # …but surfaces it when curated entities are explicitly included.
    assert audit.prelaunch_warnings(text, exclude_curated=False) != []


# ── compile_prelaunch_patterns word-boundary (shared with modality test) ─────

def test_compile_patterns_is_word_boundary():
    pats = audit.compile_prelaunch_patterns({"arc": ["arc"]})
    assert pats["arc"].search("deploying on Arc")
    assert not pats["arc"].search("arcade machine")


# ── _is_collision_risk (check_collision_risk's pure threshold decision) ─────

def test_collision_risk_flags_the_firm_case():
    # Inverse Finance's FiRM: mc=1, 'firm' hit 329x in ordinary feed prose.
    assert audit._is_collision_risk(mention_count=1, feed_hits=329)


def test_collision_risk_ignores_rare_words():
    # below the absolute floor — could just be coincidence, not a pattern.
    assert not audit._is_collision_risk(mention_count=1, feed_hits=10)


def test_collision_risk_ignores_proportionate_coverage():
    # common AND frequently mentioned in proportion — genuine coverage, not noise.
    assert not audit._is_collision_risk(mention_count=50, feed_hits=60)


def test_collision_risk_boundary_is_inclusive():
    assert audit._is_collision_risk(mention_count=2, feed_hits=40)
    assert not audit._is_collision_risk(mention_count=2, feed_hits=39)
