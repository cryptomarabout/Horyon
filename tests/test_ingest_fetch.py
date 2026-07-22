"""Tests for the sharded/timeout ingest fetch layer (2026-07-22 nitter firewall incident).

nitter_shard is pure (time-based round-robin); fetch_source is tested with urlopen mocked.
"""
from __future__ import annotations

import io
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app import config, ingest


# ── _split_sources ───────────────────────────────────────────────────────────

def test_split_sources_separates_nitter():
    src = ["https://nitter.net/a/rss", "https://blockworks.co/feed",
           "https://nitter.net/b/rss"]
    nitter, other = ingest._split_sources(src)
    assert nitter == ["https://nitter.net/a/rss", "https://nitter.net/b/rss"]
    assert other == ["https://blockworks.co/feed"]


# ── nitter_shard ─────────────────────────────────────────────────────────────

NITTER = [f"https://nitter.net/h{i}/rss" for i in range(10)]


def test_shard_is_deterministic(monkeypatch):
    monkeypatch.setattr(config, "NITTER_SHARDS", 3)
    now = datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc)
    assert ingest.nitter_shard(NITTER, now) == ingest.nitter_shard(NITTER, now)


def test_consecutive_cycles_cover_all_sources(monkeypatch):
    monkeypatch.setattr(config, "NITTER_SHARDS", 3)
    monkeypatch.setattr(config, "INGEST_INTERVAL_MIN", 20)
    base = datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc)
    seen: set[str] = set()
    for k in range(3):  # three consecutive 20-min cycles
        seen |= set(ingest.nitter_shard(NITTER, base + timedelta(minutes=20 * k)))
    assert seen == set(NITTER)


def test_shards_are_disjoint_within_rotation(monkeypatch):
    monkeypatch.setattr(config, "NITTER_SHARDS", 3)
    monkeypatch.setattr(config, "INGEST_INTERVAL_MIN", 20)
    base = datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc)
    a = set(ingest.nitter_shard(NITTER, base))
    b = set(ingest.nitter_shard(NITTER, base + timedelta(minutes=20)))
    assert a.isdisjoint(b)
    assert 0 < len(a) < len(NITTER)  # a strict subset each cycle


def test_single_shard_restores_full_fetch(monkeypatch):
    monkeypatch.setattr(config, "NITTER_SHARDS", 1)
    assert ingest.nitter_shard(NITTER) == NITTER


# ── fetch_source (urlopen mocked) ────────────────────────────────────────────

_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Hello</title><link>https://x.com/a/status/1</link>
<description>World content here</description></item></channel></rss>"""


def _resp(body: bytes, code: int = 200):
    m = MagicMock()
    m.getcode.return_value = code
    m.read.return_value = body
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_fetch_source_ok():
    with patch("urllib.request.urlopen", return_value=_resp(_RSS)) as mo:
        out = ingest.fetch_source("https://nitter.net/a/rss")
    assert out["ok"] and out["status"] == 200 and out["item_count"] == 1
    assert out["items"][0]["title"] == "Hello"
    # the explicit timeout is passed (the whole point of the change)
    assert mo.call_args.kwargs.get("timeout") == config.FEED_FETCH_TIMEOUT_SEC


def test_fetch_source_http_error():
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))
    with patch("urllib.request.urlopen", side_effect=err):
        out = ingest.fetch_source("https://nitter.net/a/rss")
    assert not out["ok"]
    assert "403" in (out["error"] or "")


def test_fetch_source_timeout():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        out = ingest.fetch_source("https://nitter.net/a/rss")
    assert not out["ok"]
    assert "timed out" in (out["error"] or "")


def test_fetch_source_unparseable():
    with patch("urllib.request.urlopen", return_value=_resp(b"not xml at all")):
        out = ingest.fetch_source("https://example.com/feed")
    assert not out["ok"]
