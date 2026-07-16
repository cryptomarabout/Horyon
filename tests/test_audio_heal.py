"""Tests for the same-day audio self-heal decision (app.briefing.variants_needing_heal).

Pure given rows: it takes today's digest_audio rows + whether the digest has bullets + the
current UTC time and returns which length variants the 20-min ingest cycle should re-render.
The guardrails it encodes (no bullets → nothing; only inside the [08:00, 22:00) UTC window;
never touch ready/pending/blocked; a 'failed' row only after a ≥75-min gap; a never-attempted
variant fires immediately) are the whole point — each has a test below.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.briefing import (
    AUDIO_HEAL_EARLIEST_UTC,
    _failed_marker_allowed,
    variants_needing_heal,
)

ALL = ("short", "standard", "explainer")


def _now(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 10, hour, minute, tzinfo=timezone.utc)


def _row(variant: str, status: str, created_at: datetime | None = None) -> dict:
    return {"variant": variant, "status": status,
            "created_at": created_at or _now(7, 10)}


# ── bullets / window gates ──────────────────────────────────────────────────────

def test_no_bullets_means_nothing_to_heal():
    # explainer failed, but the digest itself is empty — that is the digest self-heal's job.
    rows = [_row("explainer", "failed")]
    assert variants_needing_heal(rows, False, _now(9, 0)) == ()


def test_before_window_no_heal():
    rows = [_row("explainer", "failed", _now(7, 10))]
    assert variants_needing_heal(rows, True, _now(AUDIO_HEAL_EARLIEST_UTC - 1, 30)) == ()


def test_after_window_no_heal():
    rows = [_row("explainer", "failed", _now(7, 10))]
    assert variants_needing_heal(rows, True, _now(22, 30)) == ()


def test_at_window_open_heals():
    # A never-attempted variant is ungated by the spacing gap, so it fires the instant the
    # window opens — the cleanest probe of the window boundary itself.
    rows = [_row("short", "ready"), _row("standard", "ready")]
    assert variants_needing_heal(rows, True, _now(AUDIO_HEAL_EARLIEST_UTC, 0)) == ("explainer",)


# ── status handling ─────────────────────────────────────────────────────────────

def test_the_0708_incident_only_explainer_reheals():
    # Real shape from 2026-07-10: short+standard ready, explainer failed hours ago.
    rows = [_row("short", "ready"), _row("standard", "ready"),
            _row("explainer", "failed", _now(7, 16))]
    assert variants_needing_heal(rows, True, _now(9, 0)) == ("explainer",)


def test_blocked_variant_is_terminal_never_reheals():
    rows = [_row("short", "ready"), _row("standard", "ready"),
            _row("explainer", "blocked", _now(7, 16))]
    assert variants_needing_heal(rows, True, _now(12, 0)) == ()


def test_pending_and_ready_left_alone():
    rows = [_row("short", "pending"), _row("standard", "ready"),
            _row("explainer", "pending")]
    assert variants_needing_heal(rows, True, _now(10, 0)) == ()


def test_never_attempted_variant_fires_immediately():
    # standard rendered but the explainer row was never written (post-digest died mid-step).
    rows = [_row("short", "ready"), _row("standard", "ready")]
    assert variants_needing_heal(rows, True, _now(9, 0)) == ("explainer",)


def test_all_three_failed_all_reheal():
    rows = [_row(v, "failed", _now(7, 10)) for v in ALL]
    assert variants_needing_heal(rows, True, _now(9, 0)) == ALL


def test_no_rows_after_window_with_bullets_heals_all():
    # digest built + bullets exist, but the audio step never ran → no rows at all.
    assert variants_needing_heal([], True, _now(9, 0)) == ALL


# ── spacing gate ────────────────────────────────────────────────────────────────

def test_failed_too_recent_is_not_reretried():
    # explainer failed at 08:30; at 09:00 only 30 min later → under the 75-min gap.
    rows = [_row("short", "ready"), _row("standard", "ready"),
            _row("explainer", "failed", _now(8, 30))]
    assert variants_needing_heal(rows, True, _now(9, 0)) == ()


def test_failed_old_enough_reretries():
    rows = [_row("short", "ready"), _row("standard", "ready"),
            _row("explainer", "failed", _now(8, 30))]
    assert variants_needing_heal(rows, True, _now(9, 50)) == ("explainer",)


def test_naive_created_at_treated_as_utc():
    naive = datetime(2026, 7, 10, 8, 30)  # no tzinfo
    rows = [_row("short", "ready"), _row("standard", "ready"),
            _row("explainer", "failed", naive)]
    assert variants_needing_heal(rows, True, _now(9, 0)) == ()        # 30 min gap
    assert variants_needing_heal(rows, True, _now(9, 50)) == ("explainer",)  # 80 min gap


def test_missing_created_at_reheals():
    rows = [_row("short", "ready"), _row("standard", "ready"),
            {"variant": "explainer", "status": "failed", "created_at": None}]
    assert variants_needing_heal(rows, True, _now(9, 0)) == ("explainer",)


# ── failed-marker refresh (the spacing gate's input) ────────────────────────────
# 2026-07-15: the marker was only written when NO row existed, so created_at froze at the
# FIRST failure and every 20-min ingest cycle re-passed the 75-min gap gate — seven heal
# attempts in four hours. The marker must be re-written after every failed attempt (refreshing
# created_at) while never clobbering a finished/in-flight/fail-closed row.

def test_failed_marker_written_when_no_row_exists():
    assert _failed_marker_allowed(None) is True


def test_failed_marker_rewritten_over_a_previous_failed_marker():
    # This refresh is what makes the 75-min spacing gate measure from the LAST attempt.
    assert _failed_marker_allowed({"status": "failed"}) is True


def test_failed_marker_never_clobbers_ready_pending_or_blocked():
    for status in ("ready", "pending", "blocked"):
        assert _failed_marker_allowed({"status": status}) is False
