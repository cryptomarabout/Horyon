"""Specialized Update: ReAct agent over the feed with a search_feed tool."""
from __future__ import annotations

import logging
import re
import time

from . import analyst, db, entities, llm, memory, prompts
from .telegram_html import sanitize

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")

SEARCH_FEED_TOOL = {
    "type": "function",
    "function": {
        "name": "search_feed",
        "description": (
            "Search the crypto feed database semantically. Call multiple times, ONE "
            "keyword per call (e.g. 'aave', then 'base', then 'restaking'). Returns the "
            "most semantically relevant feed items from the last 30 days."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "a single keyword/protocol/topic, never combined",
                }
            },
            "required": ["keyword"],
        },
    },
}


def _plain(text: str, limit: int = 400) -> str:
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()
    return text[:limit]


def _format_results(rows: list[dict]) -> str:
    if not rows:
        return "No matching feed items in the last 30 days."
    lines = []
    for r in rows:
        when = r["pub_date"].date().isoformat() if r.get("pub_date") else "?"
        lines.append(
            f"[{r.get('source_type', '?')}] {r.get('creator', '')} ({when})\n"
            f"{_plain(r.get('content', ''))}\n"
            f"LINK: {r.get('link', '')}"
        )
    return "\n\n---\n\n".join(lines)


def _search_feed_impl(tool_input: dict) -> str:
    keyword = (tool_input or {}).get("keyword", "").strip()
    if not keyword:
        return "error: empty keyword"
    rows = db.search_feed(keyword)
    log.info("search_feed(%r) -> %d rows", keyword, len(rows))
    return _format_results(rows)


def run_specialized(keyword_or_text: str, chat_id: str | int) -> str:
    """Answer a keyword/question for ``chat_id``. Returns sanitized Telegram HTML."""
    user_text = (keyword_or_text or "").strip()

    # Fast path: return pre-computed entity brief if available (updated today's digest)
    try:
        brief = db.get_entity_intel_brief(user_text)
        if brief and brief.get("brief_html"):
            log.info("entity brief cache hit for %r (date=%s)", user_text, brief.get("digest_date"))
            return sanitize(brief["brief_html"])
    except Exception:
        log.debug("entity brief cache check failed (non-fatal)", exc_info=True)

    history = memory.load(chat_id)

    system = prompts.SPECIALIZED_SYSTEM
    context_parts: list[str] = []

    try:
        tvl_rows = db.get_latest_tvl()
        tvl_ctx = prompts.format_tvl_context(tvl_rows)
        if tvl_ctx:
            context_parts.append(tvl_ctx)
    except Exception:
        log.debug("could not fetch TVL for agent context", exc_info=True)

    try:
        entity_ctx = entities.build_entity_context_for_query(user_text)
        if entity_ctx:
            context_parts.append(entity_ctx)
    except Exception:
        log.debug("could not build entity context for agent", exc_info=True)

    try:
        notes_ctx = analyst.format_analyst_notes()
        if notes_ctx:
            context_parts.append(
                f"ANALYST NOTES — ongoing themes (last 7 days):\n{notes_ctx}"
            )
    except Exception:
        log.debug("could not load analyst notes for agent", exc_info=True)

    if context_parts:
        system = system + "\n\n" + "\n\n".join(context_parts)

    t0 = time.monotonic()
    raw, model = llm.run_agent(
        system=system,
        history=history,
        user_text=user_text,
        tools=[SEARCH_FEED_TOOL],
        tool_impls={"search_feed": _search_feed_impl},
    )
    duration_ms = int((time.monotonic() - t0) * 1000)
    answer = sanitize(raw)
    memory.save(chat_id, user_text, answer)
    try:
        db.record_keyword_analysis(user_text, str(chat_id), model, duration_ms=duration_ms)
    except Exception:
        log.warning("failed to record keyword analysis (migration pending?)", exc_info=True)
    return answer


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    q = " ".join(sys.argv[1:]) or "aave"
    print(run_specialized(q, chat_id="cli-test"))
