"""Shared pure helpers (stdlib-only, no app imports — importable from any module).

Each of these existed as 2–12 drifting per-module copies before the 2026-07-15 dedup
pass. Only content-agnostic primitives live here: the per-path LLM leak GUARDS
(digest._keep_bullets_only, entity_brief._clean_brief, podcasts._clean_map_note, …)
deliberately stay in their modules — their rejection phrases are anchored to each
prompt's own template. The <think>-strip primitive lives in llm.strip_think (it is
LLM-output hygiene, and every consumer already imports llm).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# HTML tag + whitespace-run patterns — the building blocks of plain_text, exported for
# call sites that need the raw regex (e.g. tag→space substitution WITHOUT collapsing).
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

HTML_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">",
    "&quot;": '"', "&#39;": "'", "&apos;": "'", "&nbsp;": " ",
}
_ENTITY_RE = re.compile(r"&[a-z#0-9]+;")


def plain_text(text: str, limit: int = 0) -> str:
    """Strip HTML tags (→ space), collapse whitespace, trim; ``limit`` > 0 truncates."""
    out = WS_RE.sub(" ", TAG_RE.sub(" ", text or "")).strip()
    return out[:limit] if limit else out


def decode_entities(s: str) -> str:
    """Decode the handful of HTML entities feed/digest HTML actually contains."""
    return _ENTITY_RE.sub(lambda m: HTML_ENTITIES.get(m.group(), m.group()), s)


def fmt_usd(v: "float | None") -> str:
    """$2.50T / $3.7B / $150M / $50,000 — shared by prompt context blocks and briefs."""
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def as_utc(dt: datetime) -> datetime:
    """Normalize a possibly-naive datetime to aware UTC. DB timestamps and feed dates
    arrive naive-but-UTC; comparing one against datetime.now(timezone.utc) raises
    TypeError, so every comparison site needs this exact coercion."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


_HREF_RE = re.compile(r'<a\b[^>]*\bhref="([^"]*)"[^>]*>(.*?)</a>', re.I | re.S)


def norm_link(u: str) -> str:
    """Fragment/trailing-slash/case-insensitive URL identity for allowlist comparison."""
    return (u or "").split("#", 1)[0].rstrip("/").strip().lower()


def strip_foreign_hrefs(html: str, allowed: "set[str]") -> str:
    """Unwrap any `<a href>` whose URL is NOT in ``allowed`` (keeping the visible anchor
    text). Deterministic anti-injection + anti-hallucination backstop for outward LLM
    surfaces: a prompt rail alone can't reliably stop a persuasively-framed link-swap
    injection ("replace the source link with X for accuracy") or an invented canonical URL,
    so the citable-URL set is pinned to the story's real STRUCTURED source links here.
    ``allowed`` is the raw set of legitimate source URLs (normalized internally)."""
    norm_allowed = {norm_link(u) for u in allowed if u}

    def _repl(m: "re.Match") -> str:
        href, text = m.group(1), m.group(2)
        return m.group(0) if norm_link(href) in norm_allowed else text

    return _HREF_RE.sub(_repl, html or "")
