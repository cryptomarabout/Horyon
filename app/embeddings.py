"""Ollama embedding wrapper (nomic-embed-text, 768-dim)."""
from __future__ import annotations

import html
import logging
import re

from ollama import Client

from . import config, util

log = logging.getLogger(__name__)

_client = Client(host=config.OLLAMA_HOST)

# Bump when the cleaning logic changes so existing rows get re-embedded.
EMBED_VERSION = 1

_URL_RE = re.compile(r"https?://\S+|\b[\w.-]+\.(?:com|net|org|io|xyz|co|eth|app)/\S*", re.I)

# nomic-embed-text has a 2048-token context. A long article/transcript chunk overflows it
# and Ollama returns 500 "input length exceeds the context length" (instead of truncating),
# which leaves the row un-embedded forever (retried every ingest cycle). Cap the input: the
# head of a document carries its topic, which is all the embedding needs for semantic recall.
# ~6000 chars ≈ 1500 tokens worst-case (4 chars/tok) — comfortably under the window.
EMBED_MAX_CHARS = 6000


def _truncate(text: str, limit: int) -> str:
    """Hard char cap, snapped back to the last word boundary when one is reasonably close."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    return cut[:sp] if sp > limit * 0.6 else cut


def clean_for_embedding(text: str) -> str:
    """Strip HTML tags/entities and URLs, collapse whitespace — for embeddings.

    Falls back to the tag-stripped text if URL removal would empty it, so a
    URL-only item still produces something embeddable.
    """
    stripped = util.WS_RE.sub(" ", html.unescape(util.TAG_RE.sub(" ", text or ""))).strip()
    no_urls = util.WS_RE.sub(" ", _URL_RE.sub(" ", stripped)).strip()
    return no_urls or stripped


def embed(text: str) -> list[float]:
    """Embed a single string into a 768-dim vector.

    Truncates to EMBED_MAX_CHARS first; if a token-dense input still overflows the model's
    context window, halves and retries so a single huge item never breaks ingestion.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("cannot embed empty text")
    attempt = _truncate(text, EMBED_MAX_CHARS)
    resp = None
    for _ in range(4):
        try:
            resp = _client.embeddings(model=config.EMBED_MODEL, prompt=attempt)
            break
        except Exception as exc:  # ollama.ResponseError lives at different paths across versions
            if "context length" in str(exc).lower() and len(attempt) > 400:
                attempt = _truncate(attempt, len(attempt) // 2)
                log.warning("embed: input over context window — retrying at %d chars", len(attempt))
                continue
            raise
    vec = resp["embedding"]
    if len(vec) != config.EMBED_DIMS:
        log.warning("unexpected embedding dim: %d (expected %d)", len(vec), config.EMBED_DIMS)
    return vec
