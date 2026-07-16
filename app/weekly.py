"""Weekly crypto macro digest.

Collects market data (CMC/CoinGecko), DeFiLlama protocol/category metrics, and
last 7 days of daily news bullets, then runs the LLM with the weekly skill prompt
and persists to weekly_digest.

Entry points:
  run_weekly(trigger)                              — Monday cron (current week)
  run_weekly(trigger, week_start, week_end, ...)  — specific week / backfill
  python -m app.weekly                            — manual trigger (current week)
  python -m app.weekly --backfill                 — fill all missing weeks
  python -m app.weekly --no-persist               — dry-run, print output only
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
from datetime import date, timedelta

from . import config, db, llm, prompts
from .coinmarketcap import fetch_market_data
from .telegram_html import sanitize

log = logging.getLogger(__name__)

# Em/en dashes are banned in weekly bodies (CLAUDE.md — keep the guard at BOTH the data
# layer here and web/lib/weekly.deDash). The prompts ban them too; this is the backstop
# that keeps a relapse out of the stored content. A numeric range (3—5) collapses to a
# hyphen; every other em/en dash becomes a comma. ASCII hyphens are untouched.
_EM_NUM_DASH = re.compile(r"(?<=\d)\s*[—–]\s*(?=\d)")
_EM_DASH = re.compile(r"\s*[—–]+\s*")


def _deslop(text: str) -> str:
    return _EM_DASH.sub(", ", _EM_NUM_DASH.sub("-", text or ""))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _current_week() -> tuple[date, date]:
    """Monday–Sunday of today's UTC week."""
    today = date.today()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def _extract_rotation(raw: str) -> str:
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("ROTATION:"):
            val = stripped.split(":", 1)[-1].strip().upper()
            if val in ("BTC", "ETH", "ALT", "MIXED"):
                return val
    return "MIXED"


def _strip_rotation_line(raw: str) -> str:
    return re.sub(r"(?im)^ROTATION:\s*(BTC|ETH|ALT|MIXED)\s*\n?", "", raw, count=1)


_SECTION_BULLET_RE = re.compile(r"^\s*•", re.M)


def _build_market_snapshot(ctx: dict) -> str | None:
    """Structured market levels for the Weekly web view's Market Snapshot block.

    Derived from the same live data fed to the prompt (fetch_market_data →
    {"top50":[…], "global":{…}}), so it never costs an extra call. Returns a JSON
    string (or None when live data is unavailable, e.g. historical backfill). The
    web reads this column-first and falls back to parsing the prose when absent.
    """
    market = ctx.get("market") or {}
    g = market.get("global") or {}
    top = {(c.get("symbol") or "").upper(): c for c in (market.get("top50") or [])}
    btc_row = top.get("BTC") or {}
    eth_row = top.get("ETH") or {}
    btc = btc_row.get("price")
    eth = eth_row.get("price")

    snap = {
        "btc_price": btc,
        "btc_7d_pct": btc_row.get("change_7d"),
        "eth_price": eth,
        "eth_7d_pct": eth_row.get("change_7d"),
        "eth_btc_ratio": (eth / btc) if (btc and eth) else None,
        "btc_dominance": g.get("btc_dominance"),
        "total_market_cap_usd": g.get("total_market_cap_usd"),
        "market_cap_change_24h_pct": g.get("market_cap_change_24h_pct"),
    }
    snap = {k: v for k, v in snap.items() if v is not None}
    return json.dumps(snap) if snap else None


# --------------------------------------------------------------------------- #
# Weekly v2 — deterministic sections + per-section composition (T12)
# --------------------------------------------------------------------------- #
def _top50_by_7d(ctx: dict) -> list[dict]:
    top = (ctx.get("market") or {}).get("top50") or []
    return [c for c in top if c.get("change_7d") is not None and c.get("symbol")]


def compute_rotation(ctx: dict) -> str:
    """Deterministic BTC/ETH/ALT/MIXED classification from exact market data.

    Replaces the LLM's inconsistent self-reported ROTATION line. BTC/ETH/ALT is decided
    by whichever of {BTC 7d, ETH 7d, median alt 7d (ranks 11-50)} leads, and only when the
    lead over the runner-up is meaningful (≥1pt) — otherwise MIXED. Missing price data
    (historical backfill) → MIXED, matching the prompt's backfill rule.
    """
    rows = _top50_by_7d(ctx)
    by_sym = {c["symbol"].upper(): c for c in rows}
    btc = by_sym.get("BTC", {}).get("change_7d")
    eth = by_sym.get("ETH", {}).get("change_7d")
    alts = [c["change_7d"] for c in rows if (c.get("rank") or 0) > 10]
    if btc is None or eth is None or not alts:
        return "MIXED"
    scores = {"BTC": btc, "ETH": eth, "ALT": statistics.median(alts)}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (winner, top_val), (_, second_val) = ranked[0], ranked[1]
    return winner if (top_val - second_val) >= 1.0 else "MIXED"


def build_movers_block(ctx: dict) -> str | None:
    """The '🏆 Top Movers (7d)' block built DETERMINISTICALLY — exactly 5 gainers + 5
    losers from top50, so movers are 5+5 by construction (never the LLM mis-transcribing
    exact data). Returns None on historical backfill (no price data) → the section renders
    'Data unavailable for historical backfill' instead."""
    rows = _top50_by_7d(ctx)
    if not rows:
        return None
    gainers = sorted(rows, key=lambda c: c["change_7d"], reverse=True)[:5]
    losers = sorted(rows, key=lambda c: c["change_7d"])[:5]

    def _fmt(coins: list[dict]) -> str:
        return ", ".join(f"{c['symbol'].upper()} {prompts._fmt_pct(c['change_7d'])}"
                         for c in coins)

    return ("<b>🏆 Top Movers (7d)</b>\n"
            f"• <b>Gainers:</b> {_fmt(gainers)}\n"
            f"• <b>Losers:</b> {_fmt(losers)}")


_SECTION_ORDER = ["market", "movers", "defi", "trending", "stories", "watch"]


def validate_weekly_section(key: str, text: str) -> list[str]:
    """Per-section structural validation (HIGH severity → the section is retried)."""
    errors: list[str] = []
    body = (text or "").strip()
    if not body:
        return [f"HIGH: {key} section is empty"]
    backfill = "data unavailable for historical backfill" in body.lower()
    bulleted = {"defi", "trending", "stories"}
    if key in bulleted and not backfill and not _SECTION_BULLET_RE.search(body):
        errors.append(f"HIGH: {key} section has no bullets")
    if re.search(r"[—–]", body):
        errors.append(f"MEDIUM: {key} section contains an em/en dash")
    if "example.com" in body.lower() or "]( " in body:
        errors.append(f"HIGH: {key} section has a placeholder/markdown link")
    return errors


def _build_section(key: str, ctx: dict, week_start: date, week_end: date) -> str:
    """One section via a small completion, retried once on HIGH-severity validation.
    Raises on total failure so the caller can fall back to the monolithic path."""
    system = prompts.build_weekly_section_system(key)
    user = prompts.build_weekly_section_user(key, ctx, week_start, week_end)
    header = prompts.WEEKLY_SECTIONS[key]["header"]
    best = ""
    for attempt in range(2):
        raw, _model = llm.complete(system, user, max_tokens=600, temperature=0.45)
        body = _deslop(sanitize(llm.strip_think(raw)).strip())
        # The model is told not to repeat the header, but strip it if it leaks back in.
        if body.startswith(header):
            body = body[len(header):].lstrip()
        best = body or best
        errs = validate_weekly_section(key, body)
        high = [e for e in errs if e.startswith("HIGH")]
        if errs:
            log.warning("weekly[%s]: validation (attempt %d/2): %s", key, attempt + 1,
                        "; ".join(errs))
        if not high and body:
            break
    if not best:
        raise ValueError(f"weekly section {key!r} produced no content after retry")
    return f"{header}\n{best}"


def build_sectioned_body(ctx: dict, week_start: date, week_end: date) -> tuple[str, str]:
    """Compose the weekly body per-section (T12). Returns (body_html, rotation).

    Movers + rotation are deterministic; the five prose sections are separate small
    completions with shared rails, each retried alone. Raises on any section's total
    failure so run_weekly falls back to the single-call path. Output is byte-compatible
    with the monolithic format (same emoji headers, same order) → web parser untouched.
    """
    rotation = compute_rotation(ctx)
    movers = build_movers_block(ctx) or (
        "<b>🏆 Top Movers (7d)</b>\nData unavailable for historical backfill")
    blocks: dict[str, str] = {"movers": movers}
    for key in ("market", "defi", "trending", "stories", "watch"):
        blocks[key] = _build_section(key, ctx, week_start, week_end)
    body = "\n\n".join(blocks[k] for k in _SECTION_ORDER)
    return body, rotation


def validate_weekly_output(html: str) -> list[str]:
    """Structural validation of a generated weekly digest.

    Returns error strings with leading severity word (HIGH / MEDIUM).
    Empty list = valid.  run_weekly() retries once on any HIGH error.
    """
    errors: list[str] = []
    if not html or not html.strip():
        errors.append("HIGH: weekly digest is empty")
        return errors

    # 📰 Key Stories must have at least one bullet
    ks = re.search(r"📰[^\n]*Key Stories(.*?)(?=<b>⚡|$)", html, re.S | re.I)
    if not ks or not _SECTION_BULLET_RE.search(ks.group(1)):
        errors.append("HIGH: Key Stories section is empty or missing bullets")

    # ⚡ What To Watch must have non-whitespace text
    wtw = re.search(r"⚡[^\n]*What To Watch(.*?)$", html, re.S | re.I)
    if not wtw or not wtw.group(1).strip():
        errors.append("HIGH: What To Watch section is empty or missing")

    # 🔥 Trending Dapps must have at least one bullet
    td = re.search(r"🔥[^\n]*Trending Dapps(.*?)(?=<b>📰|$)", html, re.S | re.I)
    if not td or not _SECTION_BULLET_RE.search(td.group(1)):
        errors.append("MEDIUM: Trending Dapps section is empty or missing bullets")

    return errors


# --------------------------------------------------------------------------- #
# Context builder
# --------------------------------------------------------------------------- #
def _build_context(
    week_start: date | None = None,
    week_end:   date | None = None,
    historical: bool = False,
) -> dict:
    """Collect all context for the weekly prompt.

    historical=True skips live market/DeFi data (for backfill of past weeks).
    week_start/week_end constrain the digest chain to that specific week's news.
    """
    ctx: dict = {}

    if not historical:
        # ── Live market data ─────────────────────────────────────────────────
        try:
            ctx["market"] = fetch_market_data()
        except Exception:
            log.warning("weekly: market data failed", exc_info=True)
            ctx["market"] = {}

        # ── DeFi category TVL ────────────────────────────────────────────────
        try:
            ctx["category_tvl"] = db.get_protocol_category_summary()
        except Exception:
            log.warning("weekly: category TVL failed", exc_info=True)
            ctx["category_tvl"] = []

        # ── Protocol TVL movers ──────────────────────────────────────────────
        try:
            ctx["protocol_movers"] = db.get_protocol_tvl_movers(limit=12)
        except Exception:
            log.warning("weekly: protocol movers failed", exc_info=True)
            ctx["protocol_movers"] = []

        # ── DEX weekly volumes ───────────────────────────────────────────────
        try:
            from .defillama import _get as _llama_get
            dex_data = _llama_get(
                "/overview/dexs"
                "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
                "&dataType=weeklyVolume"
            )
            if dex_data and isinstance(dex_data.get("protocols"), list):
                top_dex = sorted(
                    [p for p in dex_data["protocols"] if p.get("weeklyVolume")],
                    key=lambda p: p["weeklyVolume"],
                    reverse=True,
                )[:8]
                ctx["dex_volumes"] = [
                    {"name": p.get("name"), "volume": p.get("weeklyVolume")}
                    for p in top_dex
                ]
            else:
                ctx["dex_volumes"] = []
        except Exception:
            log.warning("weekly: DEX volumes failed", exc_info=True)
            ctx["dex_volumes"] = []

    else:
        # Historical backfill: live market/protocol snapshots aren't available, but recover
        # chain TVL from the defillama_tvl time-series when the DB has it for this week.
        log.info("weekly: historical mode — recovering chain TVL from DB if available")
        try:
            ctx["chain_tvl_hist"] = db.get_chain_tvl_for_week(week_end) if week_end else {}
            if ctx["chain_tvl_hist"]:
                log.info("weekly: recovered DB chain TVL for %d chains", len(ctx["chain_tvl_hist"]))
        except Exception:
            log.warning("weekly: historical TVL recovery failed", exc_info=True)
            ctx["chain_tvl_hist"] = {}

    # ── News digest chain ─────────────────────────────────────────────────────
    try:
        if week_start is not None and week_end is not None:
            ctx["digest_chain"] = db.get_digests_for_range(week_start, week_end)
        else:
            ctx["digest_chain"] = db.get_recent_digests_text(days=7)
    except Exception:
        log.warning("weekly: digest chain failed", exc_info=True)
        ctx["digest_chain"] = []

    # ── Previous weekly digests (for continuity / trend awareness) ────────────
    try:
        ctx["weekly_chain"] = db.get_recent_weekly_digests(
            limit=3,
            before_week_start=week_start,
        )
    except Exception:
        log.warning("weekly: weekly chain failed", exc_info=True)
        ctx["weekly_chain"] = []

    # ── Synthesized narratives (Research engine) — pre-clustered themes for the Trending
    # section, so the weekly draws on already-clustered signal instead of re-deriving it. ──
    try:
        ctx["narratives"] = db.get_existing_narratives()
    except Exception:
        log.warning("weekly: narratives fetch failed", exc_info=True)
        ctx["narratives"] = []

    return ctx


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def run_weekly(
    trigger:    str  = "cron",
    week_start: date | None = None,
    week_end:   date | None = None,
    historical: bool = False,
) -> str:
    """Build, persist, and return the weekly digest HTML.

    Omit week_start/week_end to target the current calendar week.
    Set historical=True for backfill of past weeks (skips live market data).
    """
    if week_start is None or week_end is None:
        week_start, week_end = _current_week()

    t0 = time.monotonic()
    log.info(
        "weekly digest: building for %s–%s (trigger=%s, historical=%s)",
        week_start, week_end, trigger, historical,
    )

    try:
        ctx  = _build_context(week_start=week_start, week_end=week_end, historical=historical)

        rotation = "MIXED"
        body_html = ""
        model = ""

        # Weekly v2 (T12): per-section composition — deterministic movers/rotation + five
        # small, separately-retryable prose sections. Falls back to the monolithic single
        # call if the sectioned assembly fails its overall validation.
        if config.WEEKLY_SECTIONED:
            try:
                body_html, rotation = build_sectioned_body(ctx, week_start, week_end)
                model = "sectioned"
                high_errs = [e for e in validate_weekly_output(body_html) if e.startswith("HIGH")]
                if high_errs:
                    log.warning("weekly: sectioned assembly failed overall validation "
                                "(%s) — falling back to single call", "; ".join(high_errs))
                    body_html = ""
            except Exception:
                log.warning("weekly: sectioned composition failed — falling back to single call",
                            exc_info=True)
                body_html = ""

        # Monolithic single-call path (fallback, or when WEEKLY_SECTIONED is off).
        if not body_html.strip():
            user = prompts.build_weekly_user(ctx, week_start, week_end)
            for attempt in range(2):
                raw, model = llm.complete(prompts.WEEKLY_SYSTEM, user, max_tokens=1400, temperature=0.5)
                rotation  = _extract_rotation(raw)
                body_html = sanitize(_strip_rotation_line(raw))

                errs = validate_weekly_output(body_html)
                high_errs = [e for e in errs if e.startswith("HIGH")]
                if errs:
                    log.warning(
                        "weekly: validation errors (attempt %d/2): %s",
                        attempt + 1, "; ".join(errs),
                    )
                if not high_errs:
                    break
                log.warning("weekly: retrying due to HIGH-severity validation failure")

        if not body_html.strip():
            raise ValueError("weekly digest produced empty output after retry")
        high_errs = [e for e in validate_weekly_output(body_html) if e.startswith("HIGH")]
        if high_errs:
            raise ValueError(f"weekly digest failed validation after retry: {'; '.join(high_errs)}")

        duration_ms = int((time.monotonic() - t0) * 1000)
        html = (
            f"📅 <b>Weekly Crypto Macro · "
            f"{week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}</b>\n\n"
            f"{body_html}"
        )

        db.insert_weekly_digest(
            week_start, week_end, html,
            rotation=rotation, model_used=model,
            trigger=trigger, duration_ms=duration_ms,
            market_snapshot=_build_market_snapshot(ctx),
        )
        log.info(
            "weekly digest: done — %dms, rotation=%s, model=%s",
            duration_ms, rotation, model,
        )
        return html

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("weekly digest failed (trigger=%s)", trigger)
        try:
            db.insert_weekly_digest(
                week_start, week_end, "",
                model_used="", trigger=trigger,
                duration_ms=duration_ms, error=str(exc),
            )
        except Exception:
            log.exception("failed to persist weekly digest error record")
        raise


# --------------------------------------------------------------------------- #
# CLI — manual trigger / backfill
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    ap = argparse.ArgumentParser(description="Generate and persist the weekly digest")
    ap.add_argument("--no-persist", action="store_true",
                    help="Build only — do not write to DB (current week)")
    ap.add_argument("--backfill", action="store_true",
                    help="Generate weekly digests for all weeks that have daily digests "
                         "but no weekly digest yet. Uses historical mode (news only) for "
                         "past weeks. Oldest first so each build has prior weeks as context.")
    args = ap.parse_args()

    if args.backfill:
        weeks = db.get_weeks_needing_backfill()
        if not weeks:
            print("Nothing to backfill — all weeks already have a weekly digest.")
        else:
            current_ws, _ = _current_week()
            print(f"Backfilling {len(weeks)} week(s) …")
            for i, (ws, we) in enumerate(weeks, 1):
                is_current  = (ws == current_ws)
                hist        = not is_current
                status_tag  = "current" if is_current else "historical"
                print(f"  [{i}/{len(weeks)}] {ws}–{we}  ({status_tag}) … ", end="", flush=True)
                try:
                    run_weekly(trigger="backfill", week_start=ws, week_end=we, historical=hist)
                    print("✓")
                except Exception as exc:
                    print(f"ERROR: {exc}")
            print("Backfill complete.")

    elif args.no_persist:
        ctx        = _build_context()
        start, end = _current_week()
        if config.WEEKLY_SECTIONED:
            body, rotation = build_sectioned_body(ctx, start, end)
            print(f"ROTATION: {rotation}  (sectioned)\n")
            print(body)
        else:
            user     = prompts.build_weekly_user(ctx, start, end)
            raw, _   = llm.complete(prompts.WEEKLY_SYSTEM, user, max_tokens=1400, temperature=0.5)
            rotation = _extract_rotation(raw)
            print(f"ROTATION: {rotation}\n")
            print(_strip_rotation_line(raw))

    else:
        print(run_weekly(trigger="manual"))
