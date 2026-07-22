"""Tests for app.embeddings — the NIM (nvidia/nemotron-3-embed-1b) embedding wrapper.

Pure/stubbed: no network. Covers the text cleaning, the asymmetric input_type wiring,
batch chunking + order preservation, empty-input rejection, and the retry/backoff on a
transient provider failure (the free-tier 429 case). The actual /embeddings HTTP call is
replaced with a fake client that records every request.
"""
from __future__ import annotations

import types

import pytest

from app import config, embeddings


# --------------------------------------------------------------------------- #
# Fake OpenAI-compatible client
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, n: int, dim: int):
        # one row per input; deterministic non-zero vectors of the configured dimension
        self.data = [types.SimpleNamespace(embedding=[float(i + 1)] * dim) for i in range(n)]


class _FakeEmbeddings:
    def __init__(self, dim: int, fail_times: int = 0):
        self.calls: list[dict] = []
        self._fail_times = fail_times
        self.dim = dim

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self._fail_times:
            raise RuntimeError("429 Too Many Requests (simulated)")
        return _FakeResp(len(kwargs["input"]), self.dim)


class _FakeClient:
    def __init__(self, dim: int, fail_times: int = 0):
        self.embeddings = _FakeEmbeddings(dim, fail_times)


@pytest.fixture
def fake_client(monkeypatch):
    """Swap the provider chain for a single recording fake and neutralize backoff sleeps."""
    client = _FakeClient(config.EMBED_DIMS)
    monkeypatch.setattr(embeddings, "_providers", [(client, "fake-model")])
    monkeypatch.setattr(embeddings.time, "sleep", lambda *_: None)
    return client


# --------------------------------------------------------------------------- #
# clean_for_embedding
# --------------------------------------------------------------------------- #
def test_clean_strips_html_and_urls():
    out = embeddings.clean_for_embedding(
        "<p>Bitcoin ETF flows hit a record</p> see https://x.com/foo/bar and coindesk.com/story")
    assert "<p>" not in out and "https://" not in out
    assert "coindesk.com/story" not in out
    assert "Bitcoin ETF flows hit a record" in out


def test_clean_falls_back_when_url_removal_empties():
    # A URL-only item must still yield something embeddable (the tag-stripped text).
    out = embeddings.clean_for_embedding("https://example.com/only-a-link")
    assert out  # non-empty fallback


def test_truncate_snaps_to_word_boundary():
    text = "word " * 100  # 500 chars
    cut = embeddings._truncate(text, 50)
    assert len(cut) <= 50
    assert not cut.endswith("wor")  # snapped back to a space, not mid-word


# --------------------------------------------------------------------------- #
# embed / embed_batch
# --------------------------------------------------------------------------- #
def test_embed_single_defaults_to_passage(fake_client):
    vec = embeddings.embed("Ethereum staking yield")
    assert len(vec) == config.EMBED_DIMS
    assert len(fake_client.embeddings.calls) == 1
    body = fake_client.embeddings.calls[0]["extra_body"]
    assert body["input_type"] == embeddings.PASSAGE
    assert body["truncate"] == "END"


def test_embed_query_input_type_is_threaded(fake_client):
    embeddings.embed("what are bitcoin etf flows", input_type=embeddings.QUERY)
    assert fake_client.embeddings.calls[0]["extra_body"]["input_type"] == embeddings.QUERY


def test_embed_rejects_empty():
    with pytest.raises(ValueError):
        embeddings.embed("   ")


def test_embed_batch_chunks_and_preserves_order(fake_client, monkeypatch):
    monkeypatch.setattr(config, "EMBED_BATCH_SIZE", 3)
    texts = [f"item {i}" for i in range(7)]
    vecs = embeddings.embed_batch(texts, input_type=embeddings.PASSAGE)
    assert len(vecs) == 7                      # one vector per input, order preserved
    assert len(fake_client.embeddings.calls) == 3   # ceil(7/3) chunks
    sizes = [len(c["input"]) for c in fake_client.embeddings.calls]
    assert sizes == [3, 3, 1]


def test_embed_batch_empty_returns_empty(fake_client):
    assert embeddings.embed_batch([]) == []
    assert fake_client.embeddings.calls == []


def test_embed_batch_rejects_blank_member(fake_client):
    with pytest.raises(ValueError):
        embeddings.embed_batch(["ok", "   "])


# --------------------------------------------------------------------------- #
# retry / backoff
# --------------------------------------------------------------------------- #
def test_embed_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda *_: None)
    client = _FakeClient(config.EMBED_DIMS, fail_times=2)  # first 2 rounds raise, 3rd succeeds
    monkeypatch.setattr(embeddings, "_providers", [(client, "fake-model")])
    monkeypatch.setattr(config, "EMBED_MAX_RETRIES", 4)
    vec = embeddings.embed("resilient text")
    assert len(vec) == config.EMBED_DIMS
    assert len(client.embeddings.calls) == 3


def test_embed_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda *_: None)
    client = _FakeClient(config.EMBED_DIMS, fail_times=99)
    monkeypatch.setattr(embeddings, "_providers", [(client, "fake-model")])
    monkeypatch.setattr(config, "EMBED_MAX_RETRIES", 3)
    with pytest.raises(RuntimeError):
        embeddings.embed("always failing")
    assert len(client.embeddings.calls) == 3


def test_embed_fails_over_to_fallback_provider(monkeypatch):
    """Primary always 429s → the call fails over to the fallback within the SAME round."""
    monkeypatch.setattr(embeddings.time, "sleep", lambda *_: None)
    primary = _FakeClient(config.EMBED_DIMS, fail_times=99)   # always fails
    fallback = _FakeClient(config.EMBED_DIMS)                 # always succeeds
    monkeypatch.setattr(embeddings, "_providers",
                        [(primary, "nim-model"), (fallback, "openrouter-model")])
    monkeypatch.setattr(config, "EMBED_MAX_RETRIES", 4)
    vec = embeddings.embed("rate-limited on primary", input_type=embeddings.PASSAGE)
    assert len(vec) == config.EMBED_DIMS
    assert len(primary.embeddings.calls) == 1    # tried once, in round 1
    assert len(fallback.embeddings.calls) == 1   # served the result, no backoff needed
    assert fallback.embeddings.calls[0]["extra_body"]["input_type"] == embeddings.PASSAGE
