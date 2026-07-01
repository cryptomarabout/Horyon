"""Tiny stdlib-only HTTP helper — the ONE place that builds urllib requests.

The bot fetches from a handful of free JSON / HTML / image endpoints (DeFiLlama,
CoinGecko, Snapshot, Kaiko, YouTube, unavatar, the internal web container). Every
caller used to hand-roll the identical
``urllib.request.Request(url, headers={"User-Agent": ...})`` + ``urlopen`` +
read / charset / content-type unpacking. That boilerplate now lives here.

`fetch()` raises on transport errors (``urllib.error.HTTPError`` / ``URLError``) —
callers keep their own error/logging policy by catching around it, exactly as they
did around the inline ``urlopen``. Nothing here swallows or logs, so behaviour at
every call site is unchanged.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_UA = "Horyon/1.0"
DEFAULT_TIMEOUT = 30


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str          # lowercased, params stripped (e.g. "application/json")
    charset: str               # from Content-Type, else "utf-8"
    headers: Mapping[str, str]

    def json(self) -> Any:
        return json.loads(self.body)

    def text(self, errors: str = "ignore") -> str:
        return self.body.decode(self.charset, errors)


def fetch(
    url: str,
    *,
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    ua: str = DEFAULT_UA,
    max_bytes: int | None = None,
) -> Response:
    """Perform one request and return a :class:`Response`.

    A ``User-Agent`` of ``ua`` is set first, then ``headers`` merge on top (so a
    caller can override it). Passing ``data`` makes it a POST. ``max_bytes``, when
    set, reads at most ``max_bytes + 1`` bytes (so callers can detect overflow).
    Raises on any transport error — the caller decides whether to swallow.
    """
    hdrs = {"User-Agent": ua}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
        charset = resp.headers.get_content_charset() or "utf-8"
        ctype = (resp.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        return Response(
            status=getattr(resp, "status", 200),
            body=raw,
            content_type=ctype,
            charset=charset,
            headers=dict(resp.headers),
        )


def get_json(url: str, **kw) -> Any:
    """Fetch and JSON-decode. Raises on transport or decode error."""
    return fetch(url, **kw).json()


def get_text(url: str, **kw) -> str:
    """Fetch and decode as text using the response charset. Raises on transport error."""
    return fetch(url, **kw).text()
