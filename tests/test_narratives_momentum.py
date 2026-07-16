"""Tests for app.narratives._momentum — the state machine behind the Research board's
trajectory tags (forming/heating/steady/cooling/dormant).

Pins the 2026-07-07 recalibration (heating ρ≥1.15 ∧ R≥0.5; cooling on silent-with-baseline;
forming ≤96h/n≤4): the +1/+1 smoothing squashes ρ into ~[0.77, 1.18] at real signal masses,
so the old gates (ρ≥1.5 ∧ R≥1.2, cooling ρ≤0.7) left the board pinned at steady/dormant with
zero heating/forming/cooling for weeks. Pure function — no DB/LLM.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.narratives import _momentum

REF = datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc)


def _sig(hours_ago: float, importance: int = 60, url: str = "https://a.com/x",
         stype: str = "news") -> dict:
    return {"ts": REF - timedelta(hours=hours_ago), "importance": importance,
            "url": url, "signal_type": stype}


def _cluster(signals: list[dict]) -> dict:
    return {"signals": signals}


def test_heating_on_one_strong_recent_signal():
    # One importance-90 signal in 48h (R=0.9 ≥ 0.5) against a modest multi-week baseline —
    # under the old ρ≥1.5 ∧ R≥1.2 gates this landed "steady" and nothing ever heated.
    sigs = [
        _sig(10, importance=90, url="https://a.com/1"),
        _sig(120, importance=30, url="https://b.com/2"),
        _sig(240, importance=30, url="https://a.com/3"),
        _sig(400, importance=30, url="https://b.com/4"),
    ]
    m = _momentum(_cluster(sigs), REF)
    assert m["state"] == "heating"
    assert m["intensity_48h"] >= 0.5
    assert m["momentum_ratio"] >= 1.15


def test_cooling_when_real_baseline_goes_silent():
    # A cluster that HAD sustained coverage (B ≥ 0.15) with NOTHING in the last 48h is
    # cooling — under the old ρ≤0.7 rule the +1/+1 smoothing kept it "steady" forever.
    sigs = [
        _sig(60, importance=60, url="https://a.com/1"),
        _sig(80, importance=60, url="https://b.com/2"),
        _sig(120, importance=60, url="https://a.com/3"),
    ]
    m = _momentum(_cluster(sigs), REF)
    assert m["state"] == "cooling"
    assert m["intensity_48h"] == 0
    assert m["baseline"] >= 0.15


def test_forming_window_covers_small_young_cluster():
    # 4 signals, oldest 80h — inside the widened ≤96h / n≤4 window (the old 72h/3 made
    # "forming" nearly unreachable: a cluster needs ≥3 signals over ≥2 days to exist).
    sigs = [
        _sig(80, url="https://a.com/1"),
        _sig(60, url="https://b.com/2"),
        _sig(30, url="https://a.com/3"),
        _sig(10, url="https://b.com/4"),
    ]
    assert _momentum(_cluster(sigs), REF)["state"] == "forming"


def test_dormant_when_last_signal_over_a_week_old():
    sigs = [_sig(200), _sig(250, url="https://b.com/2"), _sig(300)]
    assert _momentum(_cluster(sigs), REF)["state"] == "dormant"


def test_steady_when_recent_but_not_surging():
    # Mature cluster (first seen weeks ago), fresh low-mass activity, flat ratio → steady
    # (R>0 so the silent-with-baseline cooling rule doesn't apply; ρ within the flat band).
    sigs = [
        _sig(20, importance=20, url="https://a.com/1"),
        _sig(100, importance=60, url="https://b.com/2"),
        _sig(200, importance=60, url="https://a.com/3"),
        _sig(350, importance=60, url="https://b.com/4"),
        _sig(500, importance=60, url="https://a.com/5"),
    ]
    assert _momentum(_cluster(sigs), REF)["state"] == "steady"


def test_single_source_domain_capped_at_steady():
    # Anti-manipulation gate unchanged by the recalibration: a would-be "heating" cluster
    # fed by ONE domain with <3 recent signals is capped at steady.
    sigs = [
        _sig(10, importance=90, url="https://a.com/1"),
        _sig(30, importance=90, url="https://a.com/2"),
        _sig(240, importance=30, url="https://a.com/3"),
        _sig(400, importance=30, url="https://a.com/4"),
    ]
    m = _momentum(_cluster(sigs), REF)
    assert m["state"] == "steady"
    assert m["delta_48h"] == 2
