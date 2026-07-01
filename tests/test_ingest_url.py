"""Tests for app.ingest._normalize_url — tracking-param stripping for dedup.

Pure function (urllib only); no network, no DB.
"""
from __future__ import annotations

from app.ingest import _is_tracking_param, _normalize_url


# ── stripping (dedup normalisation) ──────────────────────────────────────────

def test_strips_utm_params():
    assert _normalize_url(
        "https://site.com/a?utm_source=twitter&utm_medium=social"
    ) == "https://site.com/a"


def test_strips_exact_ref_and_source():
    assert _normalize_url("https://site.com/a?ref=abc") == "https://site.com/a"
    assert _normalize_url("https://site.com/a?source=rss") == "https://site.com/a"


def test_strips_fbclid_gclid():
    assert _normalize_url("https://site.com/a?fbclid=xyz&gclid=123") == "https://site.com/a"


# ── preservation (the regression these fixes address) ────────────────────────

def test_preserves_params_that_merely_start_with_ref_or_source():
    # `sourceToken` / `reference` must NOT be stripped — the old startswith over-matched.
    u = "https://dex.com/swap?sourceToken=USDC&destToken=ETH"
    assert _normalize_url(u) == u
    assert _normalize_url(
        "https://site.com/p?reference=0xabc"
    ) == "https://site.com/p?reference=0xabc"


def test_distinct_pages_stay_distinct():
    a = _normalize_url("https://dex.com/swap?sourceToken=USDC")
    b = _normalize_url("https://dex.com/swap?sourceToken=DAI")
    assert a != b   # would collapse to one (and drop b as a dup) under the old rule


# ── other normalisation invariants ───────────────────────────────────────────

def test_lowercases_host_and_strips_trailing_slash():
    assert _normalize_url("https://Site.COM/path/") == "https://site.com/path"


def test_upgrades_scheme_to_https():
    assert _normalize_url("http://site.com/a") == "https://site.com/a"


def test_is_tracking_param():
    assert _is_tracking_param("utm_source")
    assert _is_tracking_param("ref")
    assert _is_tracking_param("SOURCE")            # case-insensitive
    assert not _is_tracking_param("sourceToken")
    assert not _is_tracking_param("reference")
    assert not _is_tracking_param("q")
