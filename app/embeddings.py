"""NVIDIA NIM embedding wrapper (nvidia/nemotron-3-embed-1b, 2048-dim).

Migrated off host Ollama/nomic-embed-text (768-dim) on 2026-07-21. The model is:
  * FIXED 2048-dim — the API rejects any other `dimensions` value;
  * ASYMMETRIC — query and passage embeddings of the same text differ (cos ~0.83), so stored
    documents must embed with input_type="passage" and search queries with input_type="query"
    for correct retrieval;
  * BATCH-capable — up to ~100 inputs per /embeddings call, a big win over Ollama's one-at-a-time.

Over-length input is truncated server-side (truncate="END"); we still hard-cap chars client-side
so a pathological item can't blow the token budget. Resilience: a provider chain — NIM primary,
then the SAME model on OpenRouter (`nvidia/nemotron-3-embed-1b:free`), whose vectors are numerically
identical to NIM's (cos 1.0000), so mixing providers in the one 2048-dim column is safe. Each
/embeddings request tries every provider in order; a 429/5xx on the primary fails over to the
fallback immediately, and only if the whole chain fails does the call back off and retry. The ingest
self-heal (NULL rows re-embed next cycle) remains the last line of defence.
"""
from __future__ import annotations

import html
import logging
import re
import time

from openai import OpenAI

from . import config, util

log = logging.getLogger(__name__)

def _build_providers() -> list[tuple[OpenAI, str]]:
    """Ordered (client, model) embedding providers: NIM primary, then OpenRouter fallback.

    The fallback is added only when both its model and key are configured. Same-model, so both
    return interchangeable 2048-dim vectors.
    """
    provs = [(OpenAI(api_key=config.EMBED_API_KEY, base_url=config.EMBED_BASE_URL,
                     timeout=config.EMBED_TIMEOUT_SEC, max_retries=0), config.EMBED_MODEL)]
    if config.EMBED_FALLBACK_MODEL and config.EMBED_FALLBACK_API_KEY:
        provs.append((OpenAI(api_key=config.EMBED_FALLBACK_API_KEY, base_url=config.EMBED_FALLBACK_BASE_URL,
                             timeout=config.EMBED_TIMEOUT_SEC, max_retries=0), config.EMBED_FALLBACK_MODEL))
    return provs


_providers: list[tuple[OpenAI, str]] = _build_providers()

# Bump when the model, cleaning logic, or dimension changes so existing rows get re-embedded.
# v2: migrated Ollama/nomic (768) -> NIM nemotron-3-embed-1b (2048), asymmetric input_type.
EMBED_VERSION = 2

# Valid asymmetric roles for this model. Stored corpus = PASSAGE; live search query = QUERY.
PASSAGE = "passage"
QUERY = "query"

_URL_RE = re.compile(r"https?://\S+|\b[\w.-]+\.(?:com|net|org|io|xyz|co|eth|app)/\S*", re.I)

# The server truncates over-length input (truncate="END"), but a huge item still costs tokens and
# slows the batch. Cap the input head: it carries the topic, which is all the embedding needs for
# semantic recall. ~6000 chars ≈ 1500 tokens (4 chars/tok) — comfortably under the model window.
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


def _embed_call(inputs: list[str], input_type: str) -> list[list[float]]:
    """One /embeddings request for a list of already-cleaned, char-capped inputs.

    Walks the provider chain (NIM → OpenRouter) each round: a 429/5xx/timeout on one provider fails
    over to the next IMMEDIATELY. Only when every provider has failed in a round does the call back
    off (exponential) and retry the whole chain, up to EMBED_MAX_RETRIES rounds. Raises when the
    chain is exhausted so the caller can leave the rows for the next self-heal cycle. Verifies
    dimensionality against config so a silent model/column mismatch is logged rather than stored.
    """
    last_exc: Exception | None = None
    for round_i in range(config.EMBED_MAX_RETRIES):
        for idx, (client, model) in enumerate(_providers):
            try:
                resp = client.embeddings.create(
                    model=model,
                    input=inputs,
                    # encoding_format="float" is REQUIRED: the OpenAI SDK defaults to base64, which
                    # OpenRouter's embeddings endpoint doesn't return decodably ("No embedding data
                    # received"). NIM accepts float too, so it's set uniformly across the chain.
                    encoding_format="float",
                    extra_body={"input_type": input_type, "truncate": "END"},
                )
                vecs = [d.embedding for d in resp.data]
                if vecs and len(vecs[0]) != config.EMBED_DIMS:
                    log.warning("unexpected embedding dim: %d (expected %d)",
                                len(vecs[0]), config.EMBED_DIMS)
                if idx > 0 or round_i > 0:
                    log.info("embed succeeded via fallback provider %s (round %d)", model, round_i + 1)
                return vecs
            except Exception as exc:  # noqa: BLE001 — provider errors vary; try next provider
                last_exc = exc
                log.warning("embed via %s failed (round %d/%d): %s",
                            model, round_i + 1, config.EMBED_MAX_RETRIES, str(exc)[:200])
        if round_i < config.EMBED_MAX_RETRIES - 1:
            time.sleep(config.EMBED_RETRY_BASE_SEC * (2 ** round_i))
    raise RuntimeError(
        f"embedding failed after {config.EMBED_MAX_RETRIES} rounds over {len(_providers)} provider(s)"
    ) from last_exc


def embed(text: str, input_type: str = PASSAGE) -> list[float]:
    """Embed a single string into a 2048-dim vector.

    input_type MUST be QUERY for search queries and PASSAGE for stored corpus/documents —
    the model is asymmetric and mixing them degrades recall.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("cannot embed empty text")
    return _embed_call([_truncate(text, EMBED_MAX_CHARS)], input_type)[0]


def embed_batch(texts: list[str], input_type: str = PASSAGE) -> list[list[float]]:
    """Embed many strings, chunked into config.EMBED_BATCH_SIZE-sized /embeddings calls.

    Empty/blank inputs are rejected up front (callers already filter these); order is preserved so
    results align 1:1 with ``texts``. A chunk that fails after retries propagates — the caller
    persists whatever earlier chunks succeeded and retries the rest on the next cycle.
    """
    if not texts:
        return []
    prepared = [_truncate((t or "").strip(), EMBED_MAX_CHARS) for t in texts]
    if any(not t for t in prepared):
        raise ValueError("cannot embed empty text in batch")
    out: list[list[float]] = []
    for i in range(0, len(prepared), config.EMBED_BATCH_SIZE):
        out.extend(_embed_call(prepared[i:i + config.EMBED_BATCH_SIZE], input_type))
    return out
