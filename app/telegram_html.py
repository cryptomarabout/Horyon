"""Telegram-HTML safety: allow only the tags Telegram supports, escape the rest.

Also escapes bare ``&`` so Telegram's HTML parser never 400s on raw content.
"""
from __future__ import annotations

import re

_ALLOWED = re.compile(r"<\/?(b|i|u|a|code|pre)(\s[^>]*)?>", re.IGNORECASE)
_BARE_AMP = re.compile(r"&(?!(amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)")
TELEGRAM_MAX = 4096


def sanitize(text: str) -> str:
    if not isinstance(text, str):
        text = str(text or "")

    placeholders: list[str] = []

    def _stash(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00TAG{len(placeholders) - 1}\x00"

    text = _ALLOWED.sub(_stash, text)
    text = _BARE_AMP.sub("&amp;", text)
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    for i, tag in enumerate(placeholders):
        text = text.replace(f"\x00TAG{i}\x00", tag)
    return text.strip()


def split_message(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Split into ≤limit chunks, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            if cur:
                chunks.append(cur)
            # a single line longer than the limit gets hard-sliced
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks
