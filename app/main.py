"""Single entrypoint: Telegram webhook bot + APScheduler ingestion, one process."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application

from . import config, handlers, ingest
from .telegram_html import split_message

log = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None
_app: Application | None = None
# Serializes every digest build (07:00 cron + T17 same-day retries) so a retry can
# never overlap a still-running cron build. asyncio is single-threaded; run_digest
# itself runs via to_thread, so holding this across the to_thread call is the guard.
_digest_lock = asyncio.Lock()


async def _ingest_job() -> None:
    try:
        stats = await asyncio.to_thread(ingest.run_once)
        log.info("scheduled ingest: %s", stats)
    except Exception:
        log.exception("scheduled ingest failed")

    # Safety net: if today's digest has no pre-computed bullet analyses, generate them.
    # This recovers from failures in the post-digest step (e.g. LLM timeout).
    try:
        from . import db as _db
        from .digest import generate_and_store_bullet_analyses
        from datetime import date as _date
        today = _date.today()
        digest_row = await asyncio.to_thread(lambda: _db.get_digest(today.isoformat()))
        if digest_row and digest_row.get("content"):
            existing = await asyncio.to_thread(lambda: _db.get_bullet_analyses(today))
            if not existing:
                log.info("ingest: no bullet analyses for %s — generating now", today)
                await asyncio.to_thread(
                    lambda: generate_and_store_bullet_analyses(today, digest_row["content"])
                )
    except Exception:
        log.warning("ingest: bullet analysis safety-net check failed (non-fatal)", exc_info=True)

    # Same-day digest self-heal (T17): if the 07:00 build failed and left the day empty,
    # rerun it later in the day off this 20-min cycle. Best-effort, never fatal.
    try:
        await _maybe_retry_digest()
    except Exception:
        log.warning("ingest: digest self-heal check failed (non-fatal)", exc_info=True)

    # Same-day AUDIO self-heal: if the 07:00 post-digest left an audio variant 'failed'/missing
    # (usually the heavy explainer), re-render just those later in the day. Best-effort, non-fatal.
    try:
        await _maybe_heal_audio()
    except Exception:
        log.warning("ingest: audio self-heal check failed (non-fatal)", exc_info=True)

    # Breaking-news alerts: score the items just ingested and push any that clear the balanced
    # threshold to Telegram (deterministic, no LLM). Best-effort, never fatal.
    try:
        await _maybe_send_alerts()
    except Exception:
        log.warning("ingest: breaking-news alert check failed (non-fatal)", exc_info=True)


async def _send_audio_briefing(app: Application) -> None:
    """Send today's audio briefing to each allowed chat, if one was rendered. Best-effort: a
    missing/blocked/failed render or a Telegram error is logged and swallowed — never fatal."""
    from io import BytesIO
    from . import config as _cfg, db as _db

    try:
        today = datetime.now(timezone.utc).date()
        meta = await asyncio.to_thread(lambda: _db.get_audio_briefing(today))
        if not meta or meta.get("status") != "ready" or not meta.get("has_audio"):
            return
        audio = await asyncio.to_thread(lambda: _db.get_audio_bytes(today))
        if not audio:
            return
        ext = "wav" if meta.get("mime") == "audio/wav" else "mp3"
        caption = (f"🎧 Your {today.strftime('%A')} Horyon briefing · "
                   f"full feed → {_cfg.PUBLIC_BASE_URL}")
        for chat_id in _cfg.ALLOWED_CHAT_IDS:
            try:
                await app.bot.send_audio(
                    chat_id,
                    audio=BytesIO(audio),
                    title=f"Horyon Daily Briefing · {today.strftime('%b %d')}",
                    performer="Horyon",
                    duration=meta.get("duration_sec") or None,
                    filename=f"horyon-briefing-{today}.{ext}",
                    caption=caption,
                )
            except TelegramError:
                log.exception("failed to send audio briefing to chat_id=%s", chat_id)
    except Exception:
        log.warning("audio briefing send skipped (non-fatal)", exc_info=True)


async def _send_digest_message(app: Application, html: str, *, late: bool = False) -> None:
    """Send a built digest to every allowed chat. `late=True` prepends a recovery note
    (the T17 retry path sends its own message — the Telegram send lives in the cron, not
    in run_digest)."""
    chunks = split_message(html) or ["(empty digest)"]
    if late:
        chunks[0] = ("🕐 <i>(late digest — recovered after an earlier failure this "
                     "morning)</i>\n\n") + chunks[0]
    for chat_id in config.ALLOWED_CHAT_IDS:
        try:
            for chunk in chunks:
                await app.bot.send_message(
                    chat_id, chunk,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
        except TelegramError:
            log.exception("failed to send daily digest to chat_id=%s", chat_id)


async def _maybe_retry_digest() -> None:
    """T17 same-day digest self-heal, driven off the 20-min ingest cycle. If the 07:00
    build failed and left the day empty, rerun it (≥07:20 UTC, ≤3 attempts/day, ≥60 min
    apart — decision in digest.should_retry_digest) and late-send it to Telegram. Never
    overlaps a running digest (shared _digest_lock). Best-effort; caller swallows errors."""
    from . import digest, db as _db
    from datetime import date as _date

    if _app is None or _digest_lock.locked():
        return  # a digest (cron or a prior retry) is mid-build — don't overlap
    today = _date.today()
    now = datetime.now(timezone.utc)
    attempts = await asyncio.to_thread(lambda: _db.get_digest_attempts(today))
    if not digest.should_retry_digest(attempts, now):
        return

    n = sum(1 for a in attempts if a.get("trigger") == "retry") + 1
    log.warning("digest self-heal: today's digest missing/failed — running retry #%d", n)
    async with _digest_lock:
        try:
            html = await asyncio.to_thread(lambda: digest.run_digest(trigger="retry"))
        except Exception:
            log.exception("digest self-heal: retry #%d failed", n)
            return
    log.info("digest self-heal: retry #%d succeeded — late-sending to Telegram", n)
    await _send_digest_message(_app, html, late=True)
    await _send_audio_briefing(_app)


async def _maybe_heal_audio() -> None:
    """Same-day AUDIO self-heal, driven off the 20-min ingest cycle. The 07:00 post-digest
    builds all three length variants at once, but a transient LLM outage at that minute can
    leave one 'failed' with an empty script — most often the heavy explainer (2026-07-08..10,
    when the site showed only the standard "Briefing"). This re-renders ONLY the missing/failed
    variants later in the day, when the models are reachable again. Decision is pure
    (briefing.variants_needing_heal); this just effects it. Never overlaps a digest build
    (shared _digest_lock). Best-effort; caller swallows errors."""
    from . import db as _db, briefing
    from datetime import date as _date

    if not config.AUDIO_BRIEFING_ENABLED or _app is None or _digest_lock.locked():
        return
    today = _date.today()
    now = datetime.now(timezone.utc)
    bullets = await asyncio.to_thread(lambda: _db.get_bullet_analyses(today))
    rows = await asyncio.to_thread(lambda: _db.get_audio_variant_status(today))
    need = briefing.variants_needing_heal(rows, bool(bullets), now)
    if not need:
        return

    log.warning("audio self-heal: %s missing/failed variant(s) %s — re-rendering",
                today, ", ".join(need))
    async with _digest_lock:  # keep the T17 digest retry from overlapping this build
        try:
            res = await asyncio.to_thread(
                lambda: briefing.build_all_variants_for_date(today, variants=need))
        except Exception:
            log.exception("audio self-heal: re-render failed for %s", today)
            return
    healed = [v for v in need if (res.get(v) or {}).get("status") == "ready"]
    log.info("audio self-heal: %s — rendered %s", today,
             ", ".join(f"{v}={(res.get(v) or {}).get('status', 'skip')}" for v in need))
    # Only the 'standard' show is sent to Telegram; if it was the one that healed (rare — the
    # explainer/short heal silently, the web picks them up), late-send it. Guarded so an
    # explainer-only heal never resends the standard show that already went out this morning.
    if "standard" in healed:
        await _send_audio_briefing(_app)


async def _maybe_send_alerts() -> None:
    """Breaking-news alerts off the 20-min ingest cycle (deterministic — no LLM). Scores the
    cycle's unalerted feed items, sends the alert-worthy ones to every allowed chat, then
    records them so they can never re-alert. Gated by config.ALERTS_ENABLED. Best-effort:
    the caller swallows errors, and an item is only recorded once at least one send succeeds."""
    from . import alerts
    if not config.ALERTS_ENABLED or _app is None:
        return
    found = await asyncio.to_thread(alerts.find_alertable)
    if not found:
        return
    sent: list[dict] = []
    for a in found:
        html = alerts.format_alert(a)
        delivered = False
        for chat_id in config.ALLOWED_CHAT_IDS:
            try:
                await _app.bot.send_message(
                    chat_id, html,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                delivered = True
            except TelegramError:
                log.exception("failed to send alert to chat_id=%s", chat_id)
        if delivered:
            sent.append(a)
    if sent:
        await asyncio.to_thread(lambda: alerts.mark_alerted(sent))
        log.info("alerts: sent %d breaking-news alert(s)", len(sent))


def _parse_intraday_slots(raw: str) -> list[int]:
    """Parse config.INTRADAY_SLOTS ('13,19') into a sorted list of valid UTC hours."""
    out: list[int] = []
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if tok.isdigit():
            h = int(tok)
            if 0 <= h <= 23 and h not in out:
                out.append(h)
    return sorted(out)


def _intraday_slot_label(hour: int) -> str:
    if 11 <= hour <= 15:
        return "🕐 <b>Midday Update</b>"
    if 16 <= hour <= 22:
        return "🌆 <b>Evening Update</b>"
    return "🕐 <b>Intraday Update</b>"


async def _send_intraday_message(app: Application, body: str, now: datetime) -> None:
    """Send an intraday update body to every allowed chat with a slot-aware header."""
    header = _intraday_slot_label(now.hour)
    html = f"{header}\n\n{body}\n\n<i>full feed → {config.PUBLIC_BASE_URL}</i>"
    chunks = split_message(html) or ["(empty update)"]
    for chat_id in config.ALLOWED_CHAT_IDS:
        try:
            for chunk in chunks:
                await app.bot.send_message(
                    chat_id, chunk,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
        except TelegramError:
            log.exception("failed to send intraday update to chat_id=%s", chat_id)


async def _post_init(app: Application) -> None:
    global _scheduler, _app
    _app = app

    async def _daily_digest_cron() -> None:
        from . import digest, weekly as weekly_mod  # local imports avoid circular at startup
        log.info("daily digest cron starting")
        async with _digest_lock:  # keep the T17 retry from overlapping this build
            try:
                html = await asyncio.to_thread(lambda: digest.run_digest(trigger="cron"))
            except Exception:
                log.exception("daily digest generation failed")
                return
        await _send_digest_message(app, html)

        # Daily audio briefing — generated best-effort in the post-digest orchestration above, so
        # it's already in the DB by now. Sent as a SEPARATE message right after the text so a
        # missing/blocked/failed render can never affect the digest itself.
        await _send_audio_briefing(app)

        # Refresh the weekly digest for the current (in-progress) week in the background so
        # the web /weekly preview includes today's data. Skip until the week has ≥2 daily
        # digests — a fresh Monday otherwise rebuilds a pointless 1-day "weekly" (and burns
        # a full macro-LLM call). The Monday Telegram send reads last week, not this one.
        async def _do_weekly_update() -> None:
            from . import db as _db
            from datetime import date as _date, timedelta as _td
            try:
                today = _date.today()
                week_start = today - _td(days=today.weekday())
                n_days = await asyncio.to_thread(
                    lambda: len(_db.get_digests_for_range(week_start, today))
                )
                if n_days < 2:
                    log.info(
                        "weekly update: only %d daily digest(s) this week — skipping rebuild",
                        n_days,
                    )
                    return
                await asyncio.to_thread(lambda: weekly_mod.run_weekly(trigger="daily_update"))
                log.info("weekly update complete")
            except Exception:
                log.exception("weekly daily update failed (non-fatal)")

        asyncio.create_task(_do_weekly_update())

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _ingest_job,
        "interval",
        minutes=config.INGEST_INTERVAL_MIN,
        id="rss_ingest",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),  # run once at startup
    )
    _scheduler.add_job(
        _daily_digest_cron,
        "cron",
        hour=7,   # 07:00 UTC = 09:00 UTC+2
        minute=0,
        id="daily_digest",
        max_instances=1,
        coalesce=True,
    )

    async def _intraday_update_cron() -> None:
        """Lightweight INCREMENTAL update at a fixed slot (default 13:00 + 19:00 UTC): summarize
        only feed items since the morning digest / last update, send a short Telegram message,
        and store a digest_updates row for the web timeline. No heavy post-digest pipeline.
        Shares _digest_lock so it never overlaps the 07:00 build or a self-heal retry."""
        from . import intraday
        if not config.INTRADAY_UPDATES_ENABLED:
            return
        now = datetime.now(timezone.utc)
        if _digest_lock.locked():
            log.info("intraday: a digest build is in progress — skipping the %02d:00 slot", now.hour)
            return
        log.info("intraday update cron starting (%02d:00 UTC)", now.hour)
        async with _digest_lock:
            try:
                res = await asyncio.to_thread(lambda: intraday.build_intraday_update(now))
            except Exception:
                log.exception("intraday update generation failed")
                return
        if res is None:
            log.info("intraday: nothing new enough to send this slot")
            return
        await _send_intraday_message(app, res["body"], now)

    if config.INTRADAY_UPDATES_ENABLED:
        for _slot in _parse_intraday_slots(config.INTRADAY_SLOTS):
            _scheduler.add_job(
                _intraday_update_cron,
                "cron",
                hour=_slot,
                minute=0,
                id=f"intraday_update_{_slot:02d}",
                max_instances=1,
                coalesce=True,
            )

    async def _entity_hygiene_cron() -> None:
        """Nightly self-heal of entity_memory (strip bad aliases, merge dupes, dealias
        piggybacks, purge zero-footprint junk + block re-creation). Replaces the manual
        daily audit loop; runs before the 07:00 digest so it sees a clean entity table."""
        from . import entity_hygiene
        try:
            stats = await asyncio.to_thread(entity_hygiene.run)
            log.info("entity hygiene cron: %s", stats)
        except Exception:
            log.exception("entity hygiene cron failed")

    _scheduler.add_job(
        _entity_hygiene_cron,
        "cron",
        hour=5,
        minute=20,  # 05:20 UTC — well before the daily digest
        id="entity_hygiene",
        max_instances=1,
        coalesce=True,
    )

    async def _tvl_cron() -> None:
        from . import defillama
        try:
            n = await asyncio.to_thread(defillama.fetch_and_store)
            log.info("tvl cron: stored %d rows", n)
        except Exception:
            log.exception("tvl cron failed")

    _scheduler.add_job(
        _tvl_cron,
        "cron",
        hour=7,
        minute=10,  # 07:10 UTC, just after the digest
        id="daily_tvl",
        max_instances=1,
        coalesce=True,
    )

    async def _protocols_cron() -> None:
        from . import defillama
        try:
            n = await asyncio.to_thread(defillama.fetch_and_store_protocols)
            log.info("protocols cron: upserted %d protocols", n)
        except Exception:
            log.exception("protocols cron failed")

    _scheduler.add_job(
        _protocols_cron,
        "interval",
        hours=2,
        id="protocols_poll",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    async def _market_data_cron() -> None:
        from . import defillama
        try:
            n = await asyncio.to_thread(defillama.fetch_and_store_market_data)
            log.info("market data cron: stored %d coins", n)
        except Exception:
            log.exception("market data cron failed")

    _scheduler.add_job(
        _market_data_cron,
        "interval",
        hours=2,
        id="market_data_poll",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=120),
    )

    async def _snapshot_cron() -> None:
        from . import snapshot
        try:
            n = await asyncio.to_thread(snapshot.fetch_and_store)
            log.info("snapshot cron: upserted %d proposals", n)
        except Exception:
            log.exception("snapshot cron failed")

    _scheduler.add_job(
        _snapshot_cron,
        "interval",
        minutes=30,
        id="snapshot_poll",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=90),
    )

    async def _podcast_cron() -> None:
        from . import podcasts
        try:
            stats = await asyncio.to_thread(podcasts.run_once)
            log.info("podcast cron: %s", stats)
        except Exception:
            log.exception("podcast cron failed")

    _scheduler.add_job(
        _podcast_cron,
        "interval",
        minutes=config.PODCAST_INTERVAL_MIN,
        id="podcast_ingest",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=180),
    )

    async def _podcast_prediction_recheck_cron() -> None:
        """Monthly deterministic prediction follow-through (T14): match open podcast
        predictions against later digest coverage. Zero LLM — never blocks or costs a
        call; a failure is logged and forgotten (inward surface, fail open)."""
        from . import podcasts
        try:
            stats = await asyncio.to_thread(podcasts.recheck_predictions)
            log.info("podcast prediction recheck cron: %s", stats)
        except Exception:
            log.exception("podcast prediction recheck cron failed")

    _scheduler.add_job(
        _podcast_prediction_recheck_cron,
        "cron",
        day=1, hour=6, minute=30,   # 1st of the month, quiet slot before the 07:00 digest
        id="podcast_prediction_recheck",
        max_instances=1,
        coalesce=True,
    )

    async def _kaiko_cron() -> None:
        """Ingest Kaiko Research (kaiko.com) editorial via its sitemaps — no RSS/API exists.
        Only NEW article URLs are fetched (already-stored URLs are skipped), and each run is
        capped, so steady state is a few small sitemap GETs. Items land in feed_items as
        news, feeding the digest + narratives like any other source."""
        from . import kaiko
        try:
            stats = await asyncio.to_thread(kaiko.run)
            log.info("kaiko cron: %s", stats)
        except Exception:
            log.exception("kaiko cron failed")

    if config.KAIKO_ENABLED:
        _scheduler.add_job(
            _kaiko_cron,
            "interval",
            minutes=config.KAIKO_INTERVAL_MIN,
            id="kaiko_ingest",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=240),
        )

    async def _narratives_cron() -> None:
        """Rebuild the narrative layer between digests so momentum states stay fresh
        (podcast/governance signals arrive off the daily cadence, and decay is time-based)."""
        from . import narratives
        try:
            stats = await asyncio.to_thread(narratives.build_and_store)
            log.info("narratives cron: %s", stats)
        except Exception:
            log.exception("narratives cron failed")

    _scheduler.add_job(
        _narratives_cron,
        "interval",
        hours=3,
        id="narratives_rebuild",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=300),
    )

    async def _entity_brief_refresh_cron() -> None:
        """Top up entity intel briefs between digests so a fast-moving story doesn't sit
        stale until tomorrow's 07:00 UTC digest. Self-limiting: only entities with
        meaningful new coverage since their last brief are regenerated, capped per run —
        see app.entity_brief.refresh_stale_briefs for the full cost-bound rationale."""
        from . import entity_brief
        try:
            n = await asyncio.to_thread(entity_brief.refresh_stale_briefs)
            log.info("entity brief refresh cron: refreshed %d briefs", n)
        except Exception:
            log.exception("entity brief refresh cron failed")

    _scheduler.add_job(
        _entity_brief_refresh_cron,
        "interval",
        hours=3,
        id="entity_brief_refresh",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=360),
    )

    async def _entity_graph_cron() -> None:
        """Rebuild the entity co-occurrence graph (entity_edges) read by the web map.
        Heavy scan over recent feed items → kept off the request path on a slow cron."""
        from . import entity_graph
        try:
            stats = await asyncio.to_thread(entity_graph.build_and_store)
            log.info("entity_graph cron: %s", stats)
        except Exception:
            log.exception("entity_graph cron failed")

    _scheduler.add_job(
        _entity_graph_cron,
        "interval",
        hours=6,
        id="entity_graph_rebuild",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=480),
    )

    async def _avatars_cron() -> None:
        """Mirror map-entity avatars into Postgres so the public map serves them from our
        own DB (no per-node unavatar.io stampede, no web egress). Reads entity_edges, so it
        runs after the graph rebuild. Best-effort; failures fall back to the live URL client-side."""
        from . import avatars
        try:
            stats = await asyncio.to_thread(avatars.refresh_avatars)
            log.info("avatars cron: %s", stats)
        except Exception:
            log.exception("avatars cron failed")

    _scheduler.add_job(
        _avatars_cron,
        "interval",
        hours=24,
        id="avatar_mirror",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=720),
    )

    async def _audio_retention_cron() -> None:
        """Bound digest_audio growth: NULL audio bytes older than AUDIO_RETENTION_DAYS while
        keeping scripts/chapters/metadata (294 MB — 44% of the DB — on 2026-07-07, growing
        ~5 MB/day on a 29G disk). See db.null_old_audio for the full semantics."""
        from . import db as _db
        try:
            n = await asyncio.to_thread(lambda: _db.null_old_audio(config.AUDIO_RETENTION_DAYS))
            if n:
                log.info("audio retention cron: nulled audio bytes on %d row(s)", n)
        except Exception:
            log.exception("audio retention cron failed")

    if config.AUDIO_RETENTION_DAYS > 0:
        _scheduler.add_job(
            _audio_retention_cron,
            "interval",
            hours=24,
            id="audio_retention",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=900),
        )

    async def _weekly_tg_cron() -> None:
        """Monday only: send the report for the week that JUST ENDED (last Mon–Sun).

        The daily cron's `_do_weekly_update` rebuilds the *current* (in-progress) week
        every day for the web preview — so on Monday morning the current-week row only
        holds Monday's single daily digest. The Telegram report must instead cover the
        completed previous week, which already has all 7 days. We look it up by yesterday
        (Sunday); if it's somehow missing we build it on the spot before sending.
        """
        from . import db as _db, weekly as _weekly
        from datetime import date as _date, timedelta as _timedelta
        log.info("weekly TG send starting")
        today = _date.today()
        last_sunday = today - _timedelta(days=1)   # the completed week ends yesterday
        try:
            row = await asyncio.to_thread(lambda: _db.get_weekly_for_date(last_sunday))
        except Exception:
            log.exception("weekly TG: failed to fetch digest from DB")
            return
        if not row or not row.get("content"):
            log.warning(
                "weekly TG: no digest for the week ending %s — building it now", last_sunday
            )
            last_monday = today - _timedelta(days=7)
            try:
                await asyncio.to_thread(
                    lambda: _weekly.run_weekly(
                        trigger="cron", week_start=last_monday, week_end=last_sunday
                    )
                )
                row = await asyncio.to_thread(lambda: _db.get_weekly_for_date(last_sunday))
            except Exception:
                log.exception("weekly TG: fallback build failed")
                return
        if not row or not row.get("content"):
            log.warning("weekly TG: still no digest for week ending %s — skipping", last_sunday)
            return
        html = row["content"]
        chunks = split_message(html) or ["(empty weekly digest)"]
        for chat_id in config.ALLOWED_CHAT_IDS:
            try:
                for chunk in chunks:
                    await app.bot.send_message(
                        chat_id, chunk,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
            except TelegramError:
                log.exception("failed to send weekly digest to chat_id=%s", chat_id)

    _scheduler.add_job(
        _weekly_tg_cron,
        "cron",
        day_of_week="mon",
        hour=7,
        minute=45,   # 07:45 UTC — after the daily digest (07:00) + its weekly refresh
        id="weekly_tg_send",
        max_instances=1,
        coalesce=True,
    )

    async def _eval_harness_cron() -> None:
        """Weekly scored LLM grounding + prompt-injection eval (app/eval_harness.py).
        ~18 bounded LLM calls against fixed fixtures; results land in eval_runs and
        surface on /monitor. A failure here means a model-chain/provider change
        degraded grounding — catch it Sunday evening, not in Monday's digest."""
        from . import eval_harness
        try:
            summary = await asyncio.to_thread(
                lambda: eval_harness.run_all(run_kind="weekly-cron"))
            if summary["failed_cases"]:
                log.warning("eval harness: %d/%d case(s) FAILED — see /monitor + eval_runs",
                            summary["failed_cases"], summary["cases"])
            else:
                log.info("eval harness: all %d case(s) passed", summary["cases"])
        except Exception:
            log.exception("eval harness cron failed")

    _scheduler.add_job(
        _eval_harness_cron,
        "cron",
        day_of_week="sun",
        hour=20,
        minute=10,   # quiet slot: no digest/audio work, hours from the Monday weekly
        id="llm_eval_harness",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    log.info(
        "scheduler started: ingest every %d min, daily digest 07:00 UTC, weekly TG send Mon 07:45 UTC",
        config.INGEST_INTERVAL_MIN,
    )


async def _post_shutdown(app: Application) -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)


def _check_env() -> None:
    required: dict[str, str] = {
        "TELEGRAM_BOT_TOKEN": config.TELEGRAM_BOT_TOKEN,
        "OPENROUTER_API_KEY": config.OPENROUTER_API_KEY,
    }
    if not config.BOT_USE_POLLING:
        required["TELEGRAM_WEBHOOK_BASE"] = config.TELEGRAM_WEBHOOK_BASE
        required["TELEGRAM_WEBHOOK_SECRET"] = config.TELEGRAM_WEBHOOK_SECRET
    missing = [name for name, val in required.items() if not val]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, which include the bot token — silence it.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if config.BOT_DISABLED:
        import time
        log.warning(
            "DISABLE_BOT=true — Telegram, crons, and ingest are ALL suppressed. "
            "Container is alive but idle (test/web-only mode against prod DB)."
        )
        while True:
            time.sleep(3600)

    _check_env()

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    handlers.register(app)

    if config.BOT_USE_POLLING:
        log.info("starting in POLLING mode (dev/test — not for production)")
        app.run_polling(drop_pending_updates=True)
    else:
        log.info("starting webhook at %s (listen %s:%d)",
                 config.TELEGRAM_WEBHOOK_URL, config.WEBHOOK_LISTEN_HOST, config.WEBHOOK_PORT)
        app.run_webhook(
            listen=config.WEBHOOK_LISTEN_HOST,
            port=config.WEBHOOK_PORT,
            url_path=config.TELEGRAM_WEBHOOK_PATH.lstrip("/"),
            webhook_url=config.TELEGRAM_WEBHOOK_URL,
            secret_token=config.TELEGRAM_WEBHOOK_SECRET,
            drop_pending_updates=True,
        )


if __name__ == "__main__":
    main()
