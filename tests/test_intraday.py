"""Tests for app.intraday — incremental intraday digest updates.

Covers the deterministic parts: the since-window/flooring + dedup context, the min-items
skip, the outward-LLM guard pipeline (preamble leak + foreign-href stripped), the covered-URL
pre-filter, and the persist call. The LLM + prompt builder are mocked so no network/DB is hit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app import config, intraday


def _row(content, link):
    return {"content": content, "link": link, "creator": "acct",
            "source_type": "news", "quality_flag": "ok"}


NOW = datetime(2026, 7, 21, 13, 0, tzinfo=timezone.utc)


# ── _window_and_dedup: since-ts selection + flooring ────────────────────────

def test_window_uses_last_covered_ts_when_recent():
    last = NOW - timedelta(hours=2)
    with patch("app.intraday.db") as mdb:
        mdb.get_last_covered_ts.return_value = last
        mdb.get_digest.return_value = None
        mdb.get_digest_updates.return_value = []
        since, urls, bullets = intraday._window_and_dedup(NOW.date(), NOW)
    assert since == last
    assert urls == set()


def test_window_floors_a_stale_last_ts(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_WINDOW_MAX_HOURS", 12)
    stale = NOW - timedelta(hours=30)      # older than the 12h floor
    with patch("app.intraday.db") as mdb:
        mdb.get_last_covered_ts.return_value = stale
        mdb.get_digest.return_value = None
        mdb.get_digest_updates.return_value = []
        since, _, _ = intraday._window_and_dedup(NOW.date(), NOW)
    assert since == NOW - timedelta(hours=12)


def test_window_defaults_to_floor_when_nothing_today(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_WINDOW_MAX_HOURS", 8)
    with patch("app.intraday.db") as mdb:
        mdb.get_last_covered_ts.return_value = None
        mdb.get_digest.return_value = None
        mdb.get_digest_updates.return_value = []
        since, _, _ = intraday._window_and_dedup(NOW.date(), NOW)
    assert since == NOW - timedelta(hours=8)


def test_window_collects_covered_urls():
    morning = {"content": 'x <a href="https://dup.example.com/1">🔗</a> y'}
    with patch("app.intraday.db") as mdb:
        mdb.get_last_covered_ts.return_value = NOW - timedelta(hours=2)
        mdb.get_digest.return_value = morning
        mdb.get_digest_updates.return_value = []
        _, urls, _ = intraday._window_and_dedup(NOW.date(), NOW)
    assert "https://dup.example.com/1" in urls


# ── build_intraday_update ────────────────────────────────────────────────────

def _base_mocks(mdb, rows, *, last=None):
    mdb.get_last_covered_ts.return_value = last or (NOW - timedelta(hours=2))
    mdb.get_digest.return_value = None
    mdb.get_digest_updates.return_value = []
    mdb.get_feed_items_since_ts.return_value = rows


def test_skips_when_too_few_items(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_MIN_ITEMS", 4)
    rows = [_row("A protocol did a thing worth forty plus characters here.", f"https://s/{i}")
            for i in range(2)]
    with patch("app.intraday.db") as mdb:
        _base_mocks(mdb, rows)
        res = intraday.build_intraday_update(NOW, persist=True)
    assert res is None
    mdb.insert_digest_update.assert_not_called()


def test_guards_strip_preamble_and_foreign_href(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_MIN_ITEMS", 1)
    rows = [_row("Uniswap V4 is now live on Base with real onchain activity today.",
                 "https://real.example.com/a")]
    raw = ("Sure — here's the intraday update:\n"
           "• <b>Sketchy Item</b> — Funds moved. <a href=\"https://evil.example.com/x\">🔗</a>\n"
           "• <b>Uniswap V4</b> — Now live on Base. <a href=\"https://real.example.com/a\">🔗</a>\n")
    with patch("app.intraday.db") as mdb, \
         patch("app.intraday.llm.complete", return_value=(raw, "test-model")), \
         patch("app.intraday.prompts.build_intraday_user", return_value="USER"):
        _base_mocks(mdb, rows)
        res = intraday.build_intraday_update(NOW, persist=False)
    assert res is not None
    body = res["body"]
    assert "here's the intraday update" not in body        # preamble dropped
    assert "evil.example.com" not in body                  # foreign href unwrapped
    assert "https://real.example.com/a" in body            # real source link kept
    mdb.insert_digest_update.assert_not_called()           # persist=False


def test_dedup_prefilter_drops_covered_urls(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_MIN_ITEMS", 1)
    rows = [
        _row("Story one is a genuinely fresh development worth reporting here.", "https://s/1"),
        _row("Story two was already covered in the morning digest earlier today.",
             "https://dup.example.com/1"),
        _row("Story three is another fresh development that belongs in the update.", "https://s/3"),
    ]
    morning = {"content": 'a <a href="https://dup.example.com/1">🔗</a> b'}
    raw = "• <b>Fresh One</b> — Something happened. <a href=\"https://s/1\">🔗</a>\n"
    with patch("app.intraday.db") as mdb, \
         patch("app.intraday.llm.complete", return_value=(raw, "m")), \
         patch("app.intraday.prompts.build_intraday_user", return_value="USER"):
        mdb.get_last_covered_ts.return_value = NOW - timedelta(hours=2)
        mdb.get_digest.return_value = morning
        mdb.get_digest_updates.return_value = []
        mdb.get_feed_items_since_ts.return_value = rows
        res = intraday.build_intraday_update(NOW, persist=True)
    assert res is not None
    assert res["item_count"] == 2                          # the covered URL row was dropped
    args, kwargs = mdb.insert_digest_update.call_args
    assert kwargs.get("item_count") == 2


def test_persists_with_seq_and_body(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_MIN_ITEMS", 1)
    rows = [_row("A fresh onchain development happened and it is worth a bullet here.",
                 "https://real.example.com/a")]
    raw = "• <b>Fresh Thing</b> — It happened onchain today. <a href=\"https://real.example.com/a\">🔗</a>\n"
    with patch("app.intraday.db") as mdb, \
         patch("app.intraday.llm.complete", return_value=(raw, "test-model")), \
         patch("app.intraday.prompts.build_intraday_user", return_value="USER"):
        _base_mocks(mdb, rows)
        res = intraday.build_intraday_update(NOW, persist=True)
    assert res is not None and res["seq"] == 1
    assert mdb.insert_digest_update.call_count == 1
    args, kwargs = mdb.insert_digest_update.call_args
    assert args[0] == NOW.date() and args[1] == 1          # date, seq
    assert kwargs.get("model_used") == "test-model"


def test_returns_none_when_no_bullets(monkeypatch):
    monkeypatch.setattr(config, "INTRADAY_MIN_ITEMS", 1)
    rows = [_row("Some content here that is definitely longer than forty characters.",
                 "https://s/1")]
    # LLM emits only preamble, no bullets → guards strip everything → None.
    with patch("app.intraday.db") as mdb, \
         patch("app.intraday.llm.complete", return_value=("No updates worth reporting.", "m")), \
         patch("app.intraday.prompts.build_intraday_user", return_value="USER"):
        _base_mocks(mdb, rows)
        res = intraday.build_intraday_update(NOW, persist=True)
    assert res is None
    mdb.insert_digest_update.assert_not_called()
