"""Tests for app.alerts — deterministic breaking-news alert selection + formatting.

The decision helpers (_passes, _quiet_hours, format_alert) are pure. find_alertable is
exercised with app.alerts.db mocked and scoring.score_item stubbed so we test the selection
logic (threshold, corroboration, dedup, rate cap, ranking) without a live DB.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import alerts, config


# ── _passes: the balanced decision ──────────────────────────────────────────

def _sc(score=0, source_count=0, category="", amount_usd=0.0, max_credibility=1.0):
    return {"score": score, "source_count": source_count, "category": category,
            "amount_usd": amount_usd, "max_credibility": max_credibility}


def test_passes_hard_trigger_security_large_amount():
    # A hack with a big $ magnitude alerts regardless of score/corroboration.
    ok, hard = alerts._passes(_sc(score=0, source_count=0, category="security",
                                  amount_usd=config.ALERT_HARD_AMOUNT))
    assert ok and hard


def test_passes_security_small_amount_needs_score():
    # security but tiny $ → not a hard trigger; falls through to the score gate.
    ok, hard = alerts._passes(_sc(score=10, source_count=3, category="security",
                                  amount_usd=1_000))
    assert not ok and not hard


def test_passes_score_and_two_sources():
    ok, hard = alerts._passes(_sc(score=config.ALERT_MIN_SCORE, source_count=2,
                                  category="launch"))
    assert ok and not hard


def test_passes_single_noncredible_source_rejected():
    # High score but only ONE ordinary source → blocked (single-clickbait guard).
    ok, _ = alerts._passes(_sc(score=95, source_count=1, max_credibility=1.0,
                               category="launch"))
    assert not ok


def test_passes_single_premium_source_alerts():
    # One premium/Tier-1 source (e.g. Kaiko 3.0) satisfies corroboration alone.
    ok, _ = alerts._passes(_sc(score=config.ALERT_MIN_SCORE, source_count=1,
                               max_credibility=3.0, category="funding"))
    assert ok


def test_passes_amount_without_category_rejected():
    # A price post with a big $ number but no event category is not a news alert.
    ok, _ = alerts._passes(_sc(score=90, source_count=40, amount_usd=12e9, category=""))
    assert not ok


def test_passes_below_floor_rejected():
    ok, _ = alerts._passes(_sc(score=config.ALERT_MIN_SCORE - 1, source_count=5,
                               category="launch"))
    assert not ok


# ── _quiet_hours ─────────────────────────────────────────────────────────────

def test_quiet_hours_parse(monkeypatch):
    monkeypatch.setattr(config, "ALERT_QUIET_HOURS_UTC", "0,1, 2 ,3")
    assert alerts._quiet_hours() == {0, 1, 2, 3}


def test_quiet_hours_empty(monkeypatch):
    monkeypatch.setattr(config, "ALERT_QUIET_HOURS_UTC", "")
    assert alerts._quiet_hours() == set()


# ── format_alert ─────────────────────────────────────────────────────────────

def test_format_alert_structure():
    html = alerts.format_alert({
        "title": "Aave hit by $120M exploit", "category": "security",
        "amount_usd": 120_000_000, "source_count": 3,
        "link": "https://news.example.com/aave-hack",
    })
    assert "🚨 Security" in html
    assert "$120M" in html
    assert "<b>Aave hit by $120M exploit</b>" in html
    assert 'href="https://news.example.com/aave-hack"' in html
    assert "3 sources" in html


def test_format_alert_escapes_html_in_title():
    html = alerts.format_alert({
        "title": "Bug <script>alert(1)</script> & co", "category": "",
        "amount_usd": 0, "source_count": 1, "link": "https://x.com/a/status/1",
    })
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html          # bare & escaped
    assert "1 sources" not in html  # single source → no source count tail


def test_format_alert_href_is_attribute_safe():
    # A quote in a hostile link must not break out of the href attribute.
    html = alerts.format_alert({
        "title": "T", "category": "", "amount_usd": 0, "source_count": 0,
        "link": 'https://evil.example.com/x"onclick="alert(1)',
    })
    assert 'href="https://evil.example.com/x%22onclick=%22alert(1)"' in html


# ── find_alertable (mocked db + stubbed scoring) ─────────────────────────────

def _item(title, content, link, cat="security", amt=100_000_000.0,
          score=90, src=3, cred=1.0, source_type="news"):
    """A feed item carrying the score fields the stubbed score_item echoes back."""
    return {"title": title, "content": content, "link": link, "ts": None,
            "source_type": source_type,
            "_cat": cat, "_amt": amt, "_score": score, "_src": src, "_cred": cred}


def _fake_score_item(it, ref, day, ref_time=None):
    return {"score": it["_score"], "source_count": it["_src"], "category": it["_cat"],
            "amount_usd": it["_amt"], "max_credibility": it["_cred"], "breakdown": {}}


@pytest.fixture()
def alert_env():
    """Patch app.alerts.db + scoring.build_ref_data/score_item; leave the pure scoring
    helpers (dedup, prescreen, title words) real."""
    with patch("app.alerts.db") as mdb, \
         patch("app.alerts.scoring.build_ref_data", return_value=MagicMock()), \
         patch("app.alerts.scoring.score_item", side_effect=_fake_score_item):
        mdb.count_alerts_since.return_value = 0
        mdb.get_recent_alerts.return_value = []
        mdb.get_digest.return_value = None
        mdb.get_digest_updates.return_value = []
        mdb.get_recent_unalerted_items.return_value = []
        yield mdb


def test_find_alertable_returns_qualifying(alert_env):
    alert_env.get_recent_unalerted_items.return_value = [
        _item("Aave hit by $120M exploit", "The Aave protocol suffered an exploit draining funds.",
              "https://news.example.com/aave"),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert len(out) == 1
    assert out[0]["category"] == "security"


def test_find_alertable_skips_twitter_when_news_only(alert_env):
    alert_env.get_recent_unalerted_items.return_value = [
        _item("Aave hit by $120M exploit", "Aave suffered an exploit draining funds.",
              "https://x.com/a/status/1", source_type="twitter"),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert out == []


def test_find_alertable_excludes_tradfi_topic(alert_env):
    # A news item with an event keyword but a TradFi/price theme is not an alert.
    alert_env.get_recent_unalerted_items.return_value = [
        _item("BlackRock ETF launches with record inflows",
              "The spot ETF launches today as market cap climbs on the rally.",
              "https://news.example.com/etf", cat="launch"),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert out == []


def test_find_alertable_security_bypasses_topical_exclude(alert_env):
    # A hack/exploit story mentioning an excluded word ("treasury") must STILL alert —
    # the topical exclude is for market commentary, never for incident reports.
    alert_env.get_recent_unalerted_items.return_value = [
        _item("DAO treasury drained in $60M exploit",
              "Attackers exploited the vault and drained the DAO treasury of $60M.",
              "https://news.example.com/dao-hack", cat="security", amt=60e6,
              score=90, src=3),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert len(out) == 1
    assert out[0]["category"] == "security"


def test_find_alertable_filters_low_score_uncorroborated(alert_env):
    alert_env.get_recent_unalerted_items.return_value = [
        # funding keyword passes the cheap pregate, but score below floor + 1 source → drop.
        _item("Small raise closes", "A tiny seed round raises a modest sum for the team.",
              "https://x.com/a/status/1", cat="funding", amt=0.0, score=40, src=1, cred=1.0),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert out == []


def test_same_story_clusters_different_headlines():
    from app.scoring import get_title_words as w
    a = w("Jack Mallers Quits Twenty One Capital as Tether's Bitcoin Merger Collapses")
    b = w("Jack Mallers leaves Twenty One as Strike exits Tether's three-way bitcoin merger")
    c = w("Tether-backed Twenty One, Strike merger plan scrapped: Bloomberg")
    assert alerts._same_story(a, b)
    assert alerts._same_story(a, c)


def test_same_story_keeps_different_chains_distinct():
    from app.scoring import get_title_words as w
    a = w("Aave deploys V3 lending market on Base")
    b = w("Aave deploys V3 lending market on Arbitrum")
    assert not alerts._same_story(a, b)


def test_find_alertable_dedups_same_story_in_batch(alert_env):
    alert_env.get_recent_unalerted_items.return_value = [
        _item("Twenty One Strike merger collapses as Mallers quits",
              "The Tether-backed Twenty One and Strike merger collapses; Jack Mallers quits.",
              "https://news.example.com/a", cat="funding", amt=0.0, score=90, src=30),
        _item("Mallers leaves Twenty One as Strike exits Tether merger",
              "Jack Mallers leaves Twenty One as Strike exits the Tether merger plan.",
              "https://news.example.com/b", cat="funding", amt=0.0, score=88, src=30),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert len(out) == 1                       # the second is deduped as the same story


def test_find_alertable_dedups_against_recent_alerts(alert_env):
    alert_env.get_recent_alerts.return_value = [
        {"title": "Aave hit by massive exploit", "link": "https://old/x"},
    ]
    alert_env.get_recent_unalerted_items.return_value = [
        _item("Aave hit by massive exploit today", "Aave exploit drains protocol funds again.",
              "https://news.example.com/aave2"),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert out == []


def test_find_alertable_rate_capped(alert_env):
    alert_env.count_alerts_since.return_value = config.ALERT_MAX_PER_HOUR  # budget = 0
    alert_env.get_recent_unalerted_items.return_value = [
        _item("Aave hit by $120M exploit", "The Aave protocol suffered an exploit.",
              "https://news.example.com/aave"),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert out == []


def test_find_alertable_quiet_hours(alert_env, monkeypatch):
    monkeypatch.setattr(config, "ALERT_QUIET_HOURS_UTC", "3")
    alert_env.get_recent_unalerted_items.return_value = [
        _item("Aave hit by $120M exploit", "The Aave protocol suffered an exploit.",
              "https://news.example.com/aave"),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 3, tzinfo=timezone.utc))
    assert out == []


def test_find_alertable_ranks_and_caps(alert_env, monkeypatch):
    monkeypatch.setattr(config, "ALERT_MAX_PER_HOUR", 2)
    monkeypatch.setattr(config, "ALERT_MAX_PER_DAY", 12)
    alert_env.get_recent_unalerted_items.return_value = [
        _item("Alpha protocol raises $200M round", "Alpha protocol raises a large round.",
              "https://news.example.com/alpha", cat="funding", amt=200e6, score=80, src=3),
        _item("Beta exploit drains vault", "Beta suffered an exploit that drained the vault.",
              "https://news.example.com/beta", cat="security", amt=90e6, score=95, src=4),
        _item("Gamma mainnet launches live", "Gamma mainnet launches and goes live today.",
              "https://news.example.com/gamma", cat="launch", amt=0.0, score=72, src=2),
    ]
    out = alerts.find_alertable(now=datetime(2026, 7, 21, 14, tzinfo=timezone.utc))
    assert len(out) == 2                       # capped to the hourly budget
    assert out[0]["category"] == "security"    # highest score ranks first
