"""Tests for app.entity_brief's entity-anchor gate — the pattern that keeps
semantic-search (ANN) results honest: a feed item only enters an entity's brief
prompt when it actually NAMES the entity (name or a distinctive alias), with the
ambiguous single-word brand rule (Flow/Exodus match case-sensitively) mirrored
from the shared runtime matcher. All pure — no DB, LLM, or network.
"""
from __future__ import annotations

from app.entity_brief import _chunks_for_matcher, _clean_brief, _entity_anchor, _normalize_brief_format


def _ent(name, aliases=None, **kw):
    return {"slug": name.lower(), "name": name, "type": "protocol",
            "aliases": aliases or [], **kw}


# ── term selection ───────────────────────────────────────────────────────────

def test_anchor_matches_multiword_name_case_insensitively():
    pat = _entity_anchor(_ent("Yearn Finance"))
    assert pat.search("yearn finance ships a new vault")
    assert pat.search("YEARN FINANCE TVL climbs")


def test_anchor_matches_distinctive_alias():
    pat = _entity_anchor(_ent("Ether.fi", aliases=["etherfi", "eeth"]))
    assert pat.search("eeth restaking flows accelerate")
    assert pat.search("EtherFi adds a market")
    assert not pat.search("ethereum fees fall")


def test_anchor_rejects_generic_alias():
    # "yield" is in GENERIC_TERMS — it must never anchor an item to Yield Basis.
    pat = _entity_anchor(_ent("Yield Basis", aliases=["yield"]))
    assert pat is not None                      # the multi-word name still anchors
    assert not pat.search("stablecoin yield compresses across lenders")
    assert pat.search("Yield Basis launches its AMM")


def test_anchor_rejects_handles_and_short_terms():
    pat = _entity_anchor(_ent("Morpho", aliases=["@morpholabs", "mo"]))
    assert not pat.search("follow @morpholabs for updates mo")
    assert pat.search("Morpho raises")


def test_anchor_none_when_no_usable_term():
    # Name too short + only junk aliases → no anchor (caller falls back to the name).
    assert _entity_anchor({"slug": "x", "name": "ab", "aliases": ["@x", "12"]}) is None


# ── ambiguous single-word brands: case-sensitive matching ────────────────────

def test_ambiguous_brand_requires_brand_casing():
    pat = _entity_anchor(_ent("Exodus"))
    assert not pat.search("a mass exodus of liquidity from the chain")
    assert pat.search("Exodus wallet adds swap support")
    assert pat.search("EXODUS listed on a new venue")


def test_ambiguous_brand_with_distinctive_alias_still_matches_alias():
    pat = _entity_anchor(_ent("Flow", aliases=["flow blockchain"]))
    assert not pat.search("order flow fragmented across venues")
    assert pat.search("the flow blockchain upgrade shipped")


# ── _chunks_for_matcher: pre-chunking for the 300-char shared-matcher limit ──

def test_chunks_short_text_single_chunk():
    assert _chunks_for_matcher("Aave ships a new market") == ["Aave ships a new market"]
    assert _chunks_for_matcher("") == []


def test_chunks_long_text_windows_with_overlap():
    words = [f"word{i}" for i in range(200)]
    text = " ".join(words)
    chunks = _chunks_for_matcher(text, size=120)
    assert len(chunks) > 1
    assert all(len(c) <= 120 for c in chunks)
    # tail overlap: each window re-carries the previous window's last words, so an
    # entity name straddling a boundary is still seen whole by the matcher
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.split()[-1] in nxt.split()[:6]
    # nothing lost: every word appears in some chunk
    covered = {w for c in chunks for w in c.split()}
    assert covered == set(words)


# ── _normalize_brief_format: canonicalize model formatting drift ─────────────
# Measured live 2026-07-05: 0 of 20 sampled entity_intel_brief rows had a bullet
# line starting with "•" — the model reliably drifts into these two patterns,
# and every consumer (web SearchPanel, Telegram) only ever recognized a literal
# line-leading "•", so a drifted brief rendered as empty content.

def test_normalize_splits_header_merged_with_first_bullet():
    raw = ('🔎 <b>Aave</b> • <b>V4 growth</b> — Deposits crossed $250M. '
           '<a href="https://x.com/a">🔗</a>')
    out = _normalize_brief_format(raw)
    lines = out.split("\n")
    assert lines[0] == "🔎 <b>Aave</b>"
    assert lines[1] == ""
    assert lines[2].startswith("• <b>V4 growth</b> — Deposits crossed $250M.")


def test_normalize_converts_dash_bullets_to_bullet_char():
    raw = "🔎 <b>Aave</b>\n\n- Aave V3 launched on Monad.\n- Governance quorum reached."
    out = _normalize_brief_format(raw)
    lines = [l for l in out.split("\n") if l]
    assert lines[1] == "• Aave V3 launched on Monad."
    assert lines[2] == "• Governance quorum reached."


def test_normalize_is_a_noop_on_already_canonical_text():
    raw = "🔎 <b>Aave</b>\n\n• <b>Title</b> — body. <a href=\"u\">🔗</a>"
    assert _normalize_brief_format(raw) == raw


def test_normalize_does_not_touch_em_dash_inside_bullet_body():
    # An em dash used mid-sentence (not a line-leading '-' list marker) must survive.
    raw = "🔎 <b>Aave</b>\n\n• <b>Title</b> — the real event — with detail."
    assert _normalize_brief_format(raw) == raw


def test_clean_brief_applies_normalization_end_to_end():
    raw = ('🔎 <b>Aave</b> • <b>V4 growth</b> — Deposits crossed $250M.\n'
           '- Aave V3 launched on Monad.')
    cleaned = _clean_brief(raw)
    bullet_lines = [l for l in cleaned.split("\n") if l.strip().startswith("•")]
    assert len(bullet_lines) == 2
