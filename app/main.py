"""Single entrypoint: Telegram webhook bot + APScheduler ingestion, one process."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application

from . import config, handlers, ingest
from .telegram_html import split_message

log = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


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


async def _post_init(app: Application) -> None:
    global _scheduler

    async def _daily_digest_cron() -> None:
        from . import digest, weekly as weekly_mod  # local imports avoid circular at startup
        log.info("daily digest cron starting")
        try:
            html = await asyncio.to_thread(lambda: digest.run_digest(trigger="cron"))
        except Exception:
            log.exception("daily digest generation failed")
            return
        chunks = split_message(html) or ["(empty digest)"]
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

        # Refresh weekly digest for the current week in the background (non-blocking).
        # This gives the web UI an up-to-date weekly with today's data included.
        async def _do_weekly_update() -> None:
            try:
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
        next_run_time=datetime.now(timezone.utc),
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
        next_run_time=datetime.now(timezone.utc),
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
        next_run_time=datetime.now(timezone.utc),
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
        next_run_time=datetime.now(timezone.utc),
    )

    async def _weekly_tg_cron() -> None:
        """Monday only: fetch the current week's digest from DB and send to Telegram.
        The content is already up-to-date (refreshed daily by _do_weekly_update).
        """
        from . import db as _db
        from datetime import date as _date
        log.info("weekly TG send starting")
        try:
            today = _date.today()
            row = await asyncio.to_thread(lambda: _db.get_weekly_for_date(today))
        except Exception:
            log.exception("weekly TG: failed to fetch digest from DB")
            return
        if not row or not row.get("content"):
            log.warning("weekly TG: no digest found for current week")
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
        minute=45,   # 07:45 UTC — after daily (07:00) + weekly update (~07:30)
        id="weekly_tg_send",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    log.info(
        "scheduler started: ingest every %d min, daily digest 07:00 UTC, weekly digest Mon 07:30 UTC",
        config.INGEST_INTERVAL_MIN,
    )


async def _post_shutdown(app: Application) -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)


def _check_env() -> None:
    missing = [
        name for name, val in {
            "TELEGRAM_BOT_TOKEN": config.TELEGRAM_BOT_TOKEN,
            "OPENROUTER_API_KEY": config.OPENROUTER_API_KEY,
            "TELEGRAM_WEBHOOK_BASE": config.TELEGRAM_WEBHOOK_BASE,
            "TELEGRAM_WEBHOOK_SECRET": config.TELEGRAM_WEBHOOK_SECRET,
        }.items() if not val
    ]
    if missing:
        raise SystemExit(f"Missing required env vars: {', '.join(missing)}")


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, which include the bot token — silence it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _check_env()

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    handlers.register(app)

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
