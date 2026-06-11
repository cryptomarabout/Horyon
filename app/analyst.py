"""Post-digest analyst scratchpad: extract ongoing themes + update entity summaries.

Called after every successful digest run. Runs a second (cheap) LLM pass over
the digest text to extract:
  - 3-5 forward-looking analyst notes (stored in analyst_notes)
  - Per-entity state updates (written back to entity_memory.summary)

The notes are injected into future digest prompts via build_digest_user().
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone

from . import config, db, llm, prompts

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")
_JSON_FENCE_RE = re.compile(r"```[a-z]*\n?", re.IGNORECASE)

# Strip leading "# Crypto Digest YYYY-MM-DD" header from stored digest content
_HEADER_RE = re.compile(r"^#[^\n]+\n+", re.MULTILINE)


def _strip_html(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()


def _clean_digest_content(content: str) -> str:
    """Remove the markdown header stored in crypto_digest.content."""
    return _HEADER_RE.sub("", content or "").strip()


def extract_and_persist(digest_text: str, d: date | None = None,
                        source: str = "digest") -> None:
    """Run analyst extraction on a finished digest; persist notes + entity updates.

    ``digest_text`` should be the raw (Telegram-HTML) digest as written by the LLM.
    Safe to call in a background thread — failures are logged, never re-raised.
    """
    if not digest_text or not digest_text.strip():
        return

    d = d or datetime.now(timezone.utc).date()
    plain = _strip_html(digest_text)[:6000]  # stay within model context

    try:
        raw, model = llm.complete(
            prompts.ANALYST_EXTRACTION_SYSTEM,
            prompts.build_analyst_extraction_user(plain),
            max_tokens=1100, temperature=0.25, json_mode=True,
        )
    except Exception:
        log.warning("analyst extraction LLM call failed", exc_info=True)
        return

    try:
        data = llm.parse_json_loose(raw)
    except (ValueError, TypeError):
        log.warning("analyst extraction: invalid JSON (%.120r)", raw)
        return

    notes_list = data.get("notes") or []
    entity_updates: dict[str, str] = data.get("entity_updates") or {}

    if not isinstance(notes_list, list):
        notes_list = []
    if not isinstance(entity_updates, dict):
        entity_updates = {}

    notes_text = "\n".join(
        f"• {n.strip()}" for n in notes_list[:5] if isinstance(n, str) and n.strip()
    )
    if not notes_text:
        log.debug("analyst extraction returned no notes")
        return

    # Persist analyst notes row
    try:
        db.insert_analyst_notes(d, notes_text, entity_updates,
                                source=source, model_used=model)
    except Exception:
        log.warning("failed to persist analyst notes", exc_info=True)
        return

    # Write back entity summaries (best-effort per entity)
    for slug, summary in entity_updates.items():
        slug = (slug or "").strip().lower()
        summary = (summary or "").strip()
        if not slug or not summary:
            continue
        try:
            db.update_entity_summary(slug, summary)
        except Exception:
            log.warning("failed to update entity summary for %r", slug, exc_info=True)

    log.info("analyst extraction: %d notes, %d entity updates (source=%s)",
             len(notes_list), len(entity_updates), source)


# --------------------------------------------------------------------------- #
# Context helpers — called by digest.py + specialized.py
# --------------------------------------------------------------------------- #

def format_analyst_notes(days: int | None = None) -> str:
    """Load recent analyst notes and format as a numbered list for prompt injection."""
    days = days or config.ANALYST_NOTES_DAYS
    try:
        rows = db.get_recent_analyst_notes(days)
    except Exception:
        log.debug("could not load analyst notes", exc_info=True)
        return ""
    if not rows:
        return ""
    parts: list[str] = []
    for note_date, notes in rows:
        parts.append(f"[{note_date}]\n{notes}")
    return "\n\n".join(parts)


def format_digest_chain(days: int | None = None) -> str:
    """Load recent digest texts and format as a chain context block."""
    days = days or config.DIGEST_CHAIN_DAYS
    try:
        rows = db.get_recent_digests_text(days)
    except Exception:
        log.debug("could not load digest chain", exc_info=True)
        return ""
    if not rows:
        return ""
    parts: list[str] = []
    for digest_date, content in rows:
        body = _clean_digest_content(content)
        # Limit each past digest to avoid blowing the context window
        body = _strip_html(body)[:1200].strip()
        if body:
            parts.append(f"[{digest_date}]\n{body}")
    return "\n\n".join(parts)
