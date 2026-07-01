"""Tests for app.http — the shared stdlib HTTP helper.

Network is stubbed at ``urllib.request.urlopen``; we assert the Request that gets
built (URL, merged headers, POST body) and how the Response is unpacked
(status / body / content-type / charset, and the json()/text() convenience).
"""
from __future__ import annotations

from email.message import Message
from unittest.mock import patch

from app import http


class _FakeHeaders(Message):
    """email.message.Message gives us get_content_charset() + case-insensitive get(),
    matching the real HTTPResponse.headers object closely enough for these tests."""

    def __init__(self, pairs: dict):
        super().__init__()
        for k, v in pairs.items():
            self[k] = v


class _FakeResp:
    def __init__(self, body: bytes, headers: dict, status: int = 200):
        self._body = body
        self.headers = _FakeHeaders(headers)
        self.status = status

    def read(self, n: int | None = None) -> bytes:
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(resp: _FakeResp):
    """Patch urlopen and hand back the mock so tests can inspect the built Request."""
    return patch("app.http.urllib.request.urlopen", return_value=resp)


# ── header merging + defaults ─────────────────────────────────────────────────

def test_fetch_sets_default_ua_and_merges_headers():
    resp = _FakeResp(b"{}", {"Content-Type": "application/json"})
    with _patch_urlopen(resp) as m:
        http.fetch("https://x.test/a", headers={"Accept": "application/json"})
    req = m.call_args[0][0]
    assert req.get_header("User-agent") == http.DEFAULT_UA
    assert req.get_header("Accept") == "application/json"
    assert req.full_url == "https://x.test/a"
    assert req.data is None  # GET


def test_fetch_header_can_override_ua():
    resp = _FakeResp(b"", {})
    with _patch_urlopen(resp) as m:
        http.fetch("https://x.test", ua="custom/1.0")
    assert m.call_args[0][0].get_header("User-agent") == "custom/1.0"


def test_fetch_data_makes_it_a_post():
    resp = _FakeResp(b"{}", {"Content-Type": "application/json"})
    with _patch_urlopen(resp) as m:
        http.fetch("https://x.test", data=b'{"q":1}')
    assert m.call_args[0][0].data == b'{"q":1}'


# ── Response unpacking ────────────────────────────────────────────────────────

def test_response_parses_content_type_and_charset():
    resp = _FakeResp(b"hello", {"Content-Type": "text/html; charset=latin-1"})
    with _patch_urlopen(resp):
        r = http.fetch("https://x.test")
    assert r.content_type == "text/html"        # params stripped, lowercased
    assert r.charset == "latin-1"
    assert r.status == 200


def test_response_defaults_charset_to_utf8():
    resp = _FakeResp(b"x", {"Content-Type": "image/png"})
    with _patch_urlopen(resp):
        r = http.fetch("https://x.test")
    assert r.charset == "utf-8"


def test_get_json_decodes_body():
    resp = _FakeResp(b'{"a": 1, "b": [2, 3]}', {"Content-Type": "application/json"})
    with _patch_urlopen(resp):
        assert http.get_json("https://x.test") == {"a": 1, "b": [2, 3]}


def test_get_text_uses_response_charset():
    body = "café".encode("latin-1")
    resp = _FakeResp(body, {"Content-Type": "text/plain; charset=latin-1"})
    with _patch_urlopen(resp):
        assert http.get_text("https://x.test") == "café"


def test_max_bytes_limits_read():
    resp = _FakeResp(b"0123456789", {"Content-Type": "image/png"})
    with _patch_urlopen(resp):
        r = http.fetch("https://x.test", max_bytes=4)
    # reads max_bytes + 1 so callers can detect overflow
    assert r.body == b"01234"
