"""Tests for app.digest pure text helpers — the deterministic spine that turns a
Telegram-HTML digest into structured bullets and guards what gets persisted.

The highest-value invariants here are the persist-safety ones CLAUDE.md calls out:
`_keep_bullets_only` must drop any non-bullet preamble (so a weaker fallback model's
chain-of-thought never reaches the stored digest) AND de-slop em dashes out of bullet
bodies (so an AI-tell em dash never leaks into the web/OG card/thread). All pure — no
DB, LLM, or network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.digest import (
    _build_dedup_context,
    _clean_analysis,
    _clean_text,
    _count_bullets,
    _decode,
    _deslop_bullet_body,
    _format_items,
    _is_cache_fresh,
    _keep_bullets_only,
    _parse_digest_bullets,
    _strip_tags,
    validate_digest_output,
)


# ── _decode / _strip_tags ────────────────────────────────────────────────────

def test_decode_known_entities():
    assert _decode("Tom &amp; Jerry &lt;x&gt;") == "Tom & Jerry <x>"
    assert _decode("&quot;hi&quot; &#39;yo&#39;") == "\"hi\" 'yo'"


def test_decode_leaves_unknown_entity_untouched():
    assert _decode("&zzz;") == "&zzz;"


def test_strip_tags_removes_markup():
    assert _strip_tags("<b>Aave</b> on <a href='x'>Base</a>") == "Aave on Base"


# ── _parse_digest_bullets ────────────────────────────────────────────────────

def test_parse_basic_bullet():
    html = '• <b>Aave V3</b> — deploys on <a href="https://x.com/a">Base</a> mainnet'
    out = _parse_digest_bullets(html)
    assert len(out) == 1
    assert out[0]["title"] == "Aave V3"
    assert out[0]["link"] == "https://x.com/a"
    assert "deploys on" in out[0]["body"]
    assert "<a" not in out[0]["body"]      # anchor stripped from body


def test_parse_skips_non_bullet_lines():
    html = "Here is the digest:\n• <b>Story one</b> — body\nnot a bullet\n• <b>Story two</b> — body"
    out = _parse_digest_bullets(html)
    assert [b["title"] for b in out] == ["Story one", "Story two"]


def test_parse_requires_title():
    # bullet with no <b>title</b> is dropped
    assert _parse_digest_bullets("• just some text with no title") == []


def test_parse_decodes_entities_in_title():
    out = _parse_digest_bullets("• <b>Tom &amp; Jerry</b> — body")
    assert out[0]["title"] == "Tom & Jerry"


def test_parse_link_is_none_when_absent():
    out = _parse_digest_bullets("• <b>Title</b> — plain body no link")
    assert out[0]["link"] is None


def test_parse_empty():
    assert _parse_digest_bullets("") == []


# ── _build_dedup_context ─────────────────────────────────────────────────────

def test_build_dedup_context_collects_urls_and_titles():
    rows = [
        ("2026-06-01", '• <b>Aave</b> — <a href="https://a.com/1">x</a> body'),
        ("2026-06-02", '• <b>Uniswap</b> — <a href="https://b.com/2">y</a> body'),
    ]
    urls, covered = _build_dedup_context(rows)
    assert urls == {"https://a.com/1", "https://b.com/2"}
    assert {c["title"] for c in covered} == {"Aave", "Uniswap"}
    assert {c["date"] for c in covered} == {"2026-06-01", "2026-06-02"}


def test_build_dedup_context_skips_empty_content():
    urls, covered = _build_dedup_context([("2026-06-01", None), ("2026-06-02", "")])
    assert urls == set()
    assert covered == []


# ── _clean_text / _format_items ──────────────────────────────────────────────

def test_clean_text_strips_tags_collapses_ws_and_quotes():
    assert _clean_text('<p>a   b</p>  "c"') == "a b 'c'"


def test_clean_text_caps_length():
    assert len(_clean_text("a" * 5000)) == 2000


def test_clean_text_handles_none():
    assert _clean_text(None) == ""


def test_format_items_drops_short_text():
    rows = [{"content": "too short", "source_type": "news"}]
    assert _format_items(rows) == ""


def test_format_items_renders_block():
    rows = [{
        "content": "x" * 60, "source_type": "news",
        "link": "https://a.com", "creator": "CoinDesk",
    }]
    out = _format_items(rows)
    assert "TYPE: NEWS" in out
    assert "LINK: https://a.com" in out
    assert "CREATOR: CoinDesk" in out


def test_format_items_joins_with_separator():
    rows = [
        {"content": "x" * 60, "source_type": "news", "link": "", "creator": ""},
        {"content": "y" * 60, "source_type": "social", "link": "", "creator": ""},
    ]
    assert "\n\n---\n\n" in _format_items(rows)


# ── _format_items quality_flag annotations ───────────────────────────────────
# Thin/boilerplate items stay in the prompt but carry a NOTE the model can act on —
# padding a bullet out of a 60-char teaser is the main confabulation vector.

def test_format_items_annotates_thin_content():
    rows = [{"content": "x" * 60, "source_type": "news", "link": "", "creator": "",
             "quality_flag": "thin_content"}]
    out = _format_items(rows)
    assert "NOTE: LOW-DETAIL SOURCE" in out


def test_format_items_annotates_boilerplate_title():
    rows = [{"content": "x" * 60, "source_type": "news", "link": "", "creator": "",
             "quality_flag": "boilerplate_title"}]
    out = _format_items(rows)
    assert "NOTE: NEWSLETTER/ROUNDUP" in out


def test_format_items_no_note_for_ok_or_missing_flag():
    rows = [
        {"content": "x" * 60, "source_type": "news", "link": "", "creator": "",
         "quality_flag": "ok"},
        {"content": "y" * 60, "source_type": "news", "link": "", "creator": ""},
        # nitter_handle_title: the title never reaches the prompt, content is substantive
        {"content": "z" * 60, "source_type": "twitter", "link": "", "creator": "",
         "quality_flag": "nitter_handle_title"},
    ]
    assert "NOTE:" not in _format_items(rows)


# ── _is_cache_fresh ──────────────────────────────────────────────────────────

def test_cache_fresh_recent_run():
    assert _is_cache_fresh(datetime.now(timezone.utc), "some analysis") is True


def test_cache_stale_old_run():
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    assert _is_cache_fresh(old, "analysis") is False


def test_cache_not_fresh_without_run_or_analysis():
    assert _is_cache_fresh(None, "analysis") is False
    assert _is_cache_fresh(datetime.now(timezone.utc), "") is False


# ── _deslop_bullet_body / _keep_bullets_only / _count_bullets ────────────────

def test_deslop_replaces_body_em_dash_with_comma():
    line = "• <b>Title</b> — bought at market—no VC discount"
    out = _deslop_bullet_body(line)
    assert "—no VC discount" not in out
    assert ", no VC discount" in out
    assert out.startswith("• <b>Title</b> — ")   # intentional separator preserved


def test_deslop_keeps_numeric_range_as_hyphen():
    out = _deslop_bullet_body("• <b>T</b> — raised 3—5 million")
    assert "3-5 million" in out


def test_deslop_noop_without_title():
    line = "plain line — with a dash"
    assert _deslop_bullet_body(line) == line


def test_keep_bullets_only_drops_preamble():
    body = "Here is my reasoning about the day.\n• <b>A</b> — body\n• <b>B</b> — body"
    out = _keep_bullets_only(body)
    assert "reasoning" not in out
    assert out.count("•") == 2


def test_keep_bullets_only_deslops_each_bullet():
    body = "• <b>A</b> — one—two\n• <b>B</b> — three—four"
    out = _keep_bullets_only(body)
    # The body-internal em dashes become commas; the title/body separator " — " stays.
    assert "one, two" in out and "three, four" in out
    assert out.count("—") == 2          # exactly the two separators, no body dashes left


def test_keep_bullets_only_empty():
    assert _keep_bullets_only("") == ""
    assert _keep_bullets_only(None) == ""


def test_count_bullets():
    assert _count_bullets("• a\nnot bullet\n  • b\n• c") == 3
    assert _count_bullets("") == 0


# ── _clean_analysis (2026-07-03 incident: per-bullet analyst had NO reasoning-leak guard at
# all, unlike every other LLM write-path — app/audit.py's retrospective scan found it already
# live in 14/347 stored analyses since 2026-06-04) ───────────────────────────────────────────

# A verbatim (trimmed) fragment from one of the 14 contaminated rows found in production
# (2026-07-01 · 'Phantom hires Ventuals team'): the model narrated BULLET_ANALYST_SYSTEM's own
# instructions instead of writing the analysis.
_LEAKED_ANALYSIS = (
    "We need to produce 3-4 sentences of additional context: background on the project/event, "
    "using only info present in headline, summary, and context blocks. No invented numbers. "
    "We can mention TVL $5M and that it's down 83.9% 7d? That is the ONLY source for specific "
    "numbers. So we can include those numbers. However we must not invent others."
)

_REAL_ANALYSIS = (
    "Ventuals lets traders take leveraged positions on pre-IPO company valuations without "
    "owning the underlying equity, filling a gap for retail exposure to private markets. "
    "Phantom's move signals wallets are becoming distribution channels for niche derivatives "
    "products, not just custody. Watch whether liquidity holds once the initial listing hype fades."
)


def test_clean_analysis_rejects_leaked_planning():
    assert _clean_analysis(_LEAKED_ANALYSIS) == ""


def test_clean_analysis_keeps_real_analysis_untouched():
    assert _clean_analysis(_REAL_ANALYSIS) == _REAL_ANALYSIS


def test_clean_analysis_strips_think_tags():
    wrapped = f"<think>internal deliberation, ignore</think>{_REAL_ANALYSIS}"
    assert _clean_analysis(wrapped) == _REAL_ANALYSIS


def test_clean_analysis_strips_unclosed_think_tag():
    wrapped = f"<think>internal deliberation that never closes... {_REAL_ANALYSIS}"
    assert _clean_analysis(wrapped) == ""  # nothing usable survives an unclosed think block


def test_clean_analysis_empty_input():
    assert _clean_analysis("") == ""
    assert _clean_analysis(None) == ""


# ── validate_digest_output ───────────────────────────────────────────────────

def _five_bullets(extra: str = "") -> str:
    base = "\n".join(f"• <b>Story {i}</b> — body text here" for i in range(5))
    return base + ("\n" + extra if extra else "")


def test_validate_empty_is_high_error():
    errs = validate_digest_output("")
    assert errs and errs[0].startswith("HIGH")


def test_validate_too_few_bullets():
    errs = validate_digest_output("• <b>One</b> — body")
    assert any("expected ≥5" in e for e in errs)


def test_validate_clean_five_bullets_ok():
    assert validate_digest_output(_five_bullets()) == []


def test_validate_flags_placeholder():
    errs = validate_digest_output(_five_bullets("• <b>Six</b> — N/A"))
    assert any("placeholder" in e for e in errs)


def test_validate_flags_raw_markdown():
    errs = validate_digest_output(_five_bullets("Some **bold** leak"))
    assert any("raw markdown" in e for e in errs)


def test_validate_flags_duplicate_titles():
    dupe = "\n".join(f"• <b>Same Title</b> — body {i}" for i in range(5))
    errs = validate_digest_output(dupe)
    assert any("duplicate bullet title" in e for e in errs)


def test_validate_flags_empty_body():
    errs = validate_digest_output(_five_bullets("• <b>Bodyless</b> — "))
    assert any("empty bullet body" in e for e in errs)


# ── _strip_foreign_hrefs: deterministic anti-injection/hallucination href allowlist ──
from app.digest import _strip_foreign_hrefs, _norm_link  # noqa: E402


def test_strip_foreign_hrefs_keeps_legit_source_link():
    allowed = {"https://x.com/eigenlayer/status/1944556677889900112"}
    body = ('• <b>EigenLayer slashing</b> — $12M slashed. '
            '<a href="https://x.com/eigenlayer/status/1944556677889900112#m">🔗</a>')
    out = _strip_foreign_hrefs(body, allowed)
    assert out == body  # #m fragment tolerated by _norm_link, link kept


def test_strip_foreign_hrefs_unwraps_planted_link_keeping_text():
    # link-swap injection: model cited an attacker URL not in the input source set
    allowed = {"https://x.com/eigenlayer/status/1944556677889900112"}
    body = '• <b>Story</b> — body. <a href="https://better-source.example/verified">source</a>'
    out = _strip_foreign_hrefs(body, allowed)
    assert "better-source.example" not in out
    assert "<a " not in out
    assert "source" in out  # anchor text preserved


def test_strip_foreign_hrefs_unwraps_hallucinated_canonical_url():
    allowed = {"https://theblock.co/post/1"}
    body = '• <b>Aave</b> — TVL up. <a href="https://defillama.com/protocol/aave">🔗</a>'
    out = _strip_foreign_hrefs(body, allowed)
    assert "defillama.com" not in out
    assert "🔗" in out


def test_strip_foreign_hrefs_empty_allowlist_strips_all_links():
    body = '• <b>X</b> — y. <a href="https://anything.example">z</a>'
    assert "<a " not in _strip_foreign_hrefs(body, set())


def test_norm_link_ignores_fragment_and_trailing_slash():
    assert _norm_link("https://X.com/a/#m") == _norm_link("https://x.com/a")
