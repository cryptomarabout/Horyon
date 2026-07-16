"""Tests for the shared pure helpers (app/util.py) and llm.strip_think.

These primitives replaced 2–12 drifting per-module copies (2026-07-15 dedup pass:
_plain/_fmt_usd/_decode/_strip_think and the ad-hoc tzinfo-normalization blocks), so
this suite is the single contract for all of their former call sites — digest,
entity_brief, entities, ingest, briefing, threads, podcasts, narratives, monitor.
The _fmt_usd/_plain cases were lifted from tests/test_entities.py when the helpers
moved out of app/entities.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.llm import strip_think
from app.util import as_utc, decode_entities, fmt_usd, plain_text


# ── fmt_usd ──────────────────────────────────────────────────────────────────

def test_fmt_usd_trillions():
    assert fmt_usd(2.5e12) == "$2.50T"


def test_fmt_usd_billions():
    assert fmt_usd(3.7e9) == "$3.7B"


def test_fmt_usd_millions():
    assert fmt_usd(150e6) == "$150M"


def test_fmt_usd_small():
    assert fmt_usd(50_000) == "$50,000"


def test_fmt_usd_billion_boundary():
    assert fmt_usd(1e9) == "$1.0B"


def test_fmt_usd_none_is_na():
    # The prompts.py copy was the only one that handled None; the shared helper keeps it.
    assert fmt_usd(None) == "N/A"


# ── plain_text ───────────────────────────────────────────────────────────────

def test_plain_text_strips_html_tags():
    result = plain_text("<b>Bold</b> and <i>italic</i>")
    assert "<b>" not in result
    assert "Bold" in result
    assert "italic" in result


def test_plain_text_collapses_whitespace():
    assert plain_text("hello   \t   world") == "hello world"


def test_plain_text_respects_limit():
    assert len(plain_text("a" * 500, 100)) == 100


def test_plain_text_no_limit_by_default():
    assert len(plain_text("a" * 400)) == 400


def test_plain_text_empty():
    assert plain_text("") == ""
    assert plain_text(None) == ""


def test_plain_text_tag_becomes_word_gap():
    # Tags substitute to a space so adjacent words never merge ("a</p><p>b" → "a b").
    assert plain_text("a</p><p>b") == "a b"


# ── decode_entities ──────────────────────────────────────────────────────────

def test_decode_entities_known():
    assert decode_entities("Tom &amp; Jerry &gt; cartoons") == "Tom & Jerry > cartoons"
    assert decode_entities("&quot;quoted&quot; &#39;apos&#39;") == "\"quoted\" 'apos'"


def test_decode_entities_unknown_left_alone():
    # Unmapped entities pass through untouched rather than being mangled.
    assert decode_entities("&copy; 2026") == "&copy; 2026"


# ── as_utc ───────────────────────────────────────────────────────────────────

def test_as_utc_naive_becomes_aware_utc():
    d = as_utc(datetime(2026, 7, 15, 8, 30))
    assert d.tzinfo == timezone.utc
    assert (d.hour, d.minute) == (8, 30)  # naive DB timestamps ARE UTC — no shifting


def test_as_utc_aware_unchanged():
    d = datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)
    assert as_utc(d) is d


# ── llm.strip_think ──────────────────────────────────────────────────────────

def test_strip_think_closed_block():
    assert strip_think("<think>plan plan</think>The answer.") == "The answer."


def test_strip_think_unclosed_block_drops_to_end():
    # A truncated reasoning dump with no </think> must never survive into output.
    assert strip_think("Real text. <think>and then I will") == "Real text."


def test_strip_think_case_insensitive_and_multiline():
    assert strip_think("<THINK>a\nb\nc</THINK>ok") == "ok"


def test_strip_think_no_block_passthrough():
    assert strip_think("  plain output  ") == "plain output"
    assert strip_think(None) == ""
    assert strip_think("") == ""


# ── strip_foreign_hrefs / norm_link (shared anti-injection href allowlist) ──
from app.util import strip_foreign_hrefs, norm_link  # noqa: E402


def test_norm_link_identity():
    assert norm_link("https://X.com/A/#frag") == "https://x.com/a"
    assert norm_link("") == ""


def test_strip_foreign_hrefs_keeps_allowed_unwraps_others():
    allowed = {"https://theblock.co/post/1"}
    html = ('<a href="https://theblock.co/post/1#m">a</a> '
            '<a href="https://evil.example/x">b</a>')
    out = strip_foreign_hrefs(html, allowed)
    assert 'href="https://theblock.co/post/1#m"' in out  # allowed kept (fragment tolerated)
    assert "evil.example" not in out and ">b<" not in out  # foreign unwrapped
    assert out.endswith("b")  # anchor text preserved
