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

import logging
import re
import time
from datetime import date, timedelta

from . import db, llm, prompts
from .coinmarketcap import fetch_market_data
from .telegram_html import sanitize

log = logging.getLogger(__name__)


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
        user = prompts.build_weekly_user(ctx, week_start, week_end)
        raw, model = llm.complete(prompts.WEEKLY_SYSTEM, user, max_tokens=1400, temperature=0.5)
        duration_ms = int((time.monotonic() - t0) * 1000)

        rotation  = _extract_rotation(raw)
        body_raw  = _strip_rotation_line(raw)
        body_html = sanitize(body_raw)
        html = (
            f"📅 <b>Weekly Crypto Macro · "
            f"{week_start.strftime('%b %d')}–{week_end.strftime('%b %d, %Y')}</b>\n\n"
            f"{body_html}"
        )

        db.insert_weekly_digest(
            week_start, week_end, html,
            rotation=rotation, model_used=model,
            trigger=trigger, duration_ms=duration_ms,
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
        user       = prompts.build_weekly_user(ctx, start, end)
        raw, _     = llm.complete(prompts.WEEKLY_SYSTEM, user, max_tokens=1400, temperature=0.5)
        rotation   = _extract_rotation(raw)
        print(f"ROTATION: {rotation}\n")
        print(_strip_rotation_line(raw))

    else:
        print(run_weekly(trigger="manual"))
