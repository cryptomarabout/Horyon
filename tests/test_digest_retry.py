"""Tests for the T17 same-day digest retry decision (app.digest.should_retry_digest).

Pure given rows: it takes today's crypto_digest attempts + the current UTC time and
decides whether the 20-min ingest cycle should rerun the digest. The guardrails it
encodes (never before 07:20, never rebuild a good day, ≤3 retries, ≥60 min apart, first
retry ungated by spacing) are the whole point of the task — each has a test below.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.digest import (
    DIGEST_RETRY_MAX,
    should_retry_digest,
)


def _now(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 8, hour, minute, tzinfo=timezone.utc)


def _attempt(*, trigger="cron", error=None, has_content=True, created_at=None) -> dict:
    return {
        "trigger": trigger,
        "error": error,
        "has_content": has_content,
        "created_at": created_at or _now(7, 0),
    }


def _fail(**kw) -> dict:
    """A failed cron/retry row (error set, no real content)."""
    return _attempt(error="boom", has_content=False, **kw)


# ── window gate ───────────────────────────────────────────────────────────────

def test_no_retry_before_window():
    # 07:00 build failed, but it is only 07:10 — inside the window, don't fire yet.
    assert should_retry_digest([_fail(created_at=_now(7, 0))], _now(7, 10)) is False


def test_retry_at_exactly_window_open():
    assert should_retry_digest([_fail(created_at=_now(7, 0))], _now(7, 20)) is True


def test_missing_row_after_window_retries():
    # No attempt persisted at all (e.g. cron crashed before persist) — treat as failed.
    assert should_retry_digest([], _now(7, 30)) is True


# ── success guardrail ─────────────────────────────────────────────────────────

def test_no_retry_when_digest_succeeded():
    assert should_retry_digest([_attempt(error=None, has_content=True)], _now(8, 0)) is False


def test_no_retry_when_success_came_after_a_failure():
    attempts = [
        _fail(created_at=_now(7, 0)),
        _attempt(trigger="retry", error=None, has_content=True, created_at=_now(7, 20)),
    ]
    assert should_retry_digest(attempts, _now(9, 0)) is False


def test_error_null_but_empty_content_is_not_success():
    # Degenerate: error NULL but only a bare header body — still counts as failed.
    attempts = [_attempt(error=None, has_content=False, created_at=_now(7, 0))]
    assert should_retry_digest(attempts, _now(7, 30)) is True


# ── retry cap ─────────────────────────────────────────────────────────────────

def test_retry_cap_reached():
    attempts = [_fail(created_at=_now(7, 0))]
    attempts += [
        _fail(trigger="retry", created_at=_now(7 + i, 20)) for i in range(DIGEST_RETRY_MAX)
    ]
    assert should_retry_digest(attempts, _now(12, 0)) is False


def test_below_cap_still_retries():
    attempts = [
        _fail(created_at=_now(7, 0)),
        _fail(trigger="retry", created_at=_now(7, 20)),
    ]
    # one retry so far, 90 min later, cap is 3 → allowed
    assert should_retry_digest(attempts, _now(8, 50)) is True


# ── spacing ───────────────────────────────────────────────────────────────────

def test_spacing_blocks_a_too_soon_second_retry():
    attempts = [
        _fail(created_at=_now(7, 0)),
        _fail(trigger="retry", created_at=_now(7, 20)),
    ]
    # only 20 min after the first retry → blocked by the 60-min gap
    assert should_retry_digest(attempts, _now(7, 40)) is False


def test_first_retry_not_blocked_by_the_0700_failure_spacing():
    # The 07:00 cron failure is only 20 min old at 07:20, but the FIRST retry is gated
    # only by the window, not by spacing off the cron attempt.
    assert should_retry_digest([_fail(created_at=_now(7, 0))], _now(7, 20)) is True


def test_spacing_uses_latest_retry():
    attempts = [
        _fail(created_at=_now(7, 0)),
        _fail(trigger="retry", created_at=_now(7, 20)),
        _fail(trigger="retry", created_at=_now(8, 25)),
    ]
    # latest retry 08:25; at 09:00 only 35 min later → blocked
    assert should_retry_digest(attempts, _now(9, 0)) is False
    # at 09:30 it is 65 min → allowed (still under the 3-retry cap)
    assert should_retry_digest(attempts, _now(9, 30)) is True


def test_naive_created_at_is_treated_as_utc():
    naive = datetime(2026, 7, 8, 7, 20)  # no tzinfo
    attempts = [
        _fail(created_at=_now(7, 0)),
        _fail(trigger="retry", created_at=naive),
    ]
    assert should_retry_digest(attempts, _now(7, 40)) is False   # 20 min gap
    assert should_retry_digest(attempts, _now(8, 25)) is True    # 65 min gap
