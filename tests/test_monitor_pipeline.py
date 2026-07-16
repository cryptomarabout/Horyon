"""Monitor pipeline-health panel (T9): the CONTENT template renders every red
state and degrades gracefully when the health query failed (empty dict).

Rendering with a fully-fake gather() dict also pins the Jinja template itself —
a template syntax error otherwise only surfaces at runtime on /monitor.
"""
from __future__ import annotations

from flask import render_template_string

from app import monitor


def _base_data(pipeline: dict) -> dict:
    """Minimal fake of monitor.gather()'s return shape."""
    return {
        "now": "2026-07-15 12:00:00 UTC+2",
        "is_dev": False,
        "containers": [],
        "feed": {"total": 100, "embedded": 100, "null": 0, "stale": 0,
                 "last_ingested": "2026-07-15 11:55", "last_1h": 5,
                 "last_24h": 60, "by_type": [("news", 80), ("twitter", 20)]},
        "runs": [],
        "src": {"total": 2, "ok": 2, "failed": 0,
                "twitter": {"rows": [], "ok": 1, "total": 1, "failed": 0},
                "news": {"rows": [], "ok": 1, "total": 1, "failed": 0}},
        "content": {"digests": 10, "last_digest": "2026-07-15",
                    "chat_msgs": 1, "chat_users": 1, "protocols": 100},
        "recent_digests": [],
        "recent_analyses": [],
        "tvl": [],
        "kaiko": {"total": 0, "citations": [], "articles": []},
        "pipeline": pipeline,
        "db_switch": {},
    }


def _render(pipeline: dict) -> str:
    with monitor.app.app_context():
        return render_template_string(monitor.CONTENT, **_base_data(pipeline))


RED_PIPELINE = {
    "emb_overdue": 12,
    "failing_sources": [{"display": "example.com · feed",
                         "url": "https://example.com/feed",
                         "failures": 42, "status": 404, "error": "Not Found"}],
    "audio_bad": [{"date": "2026-07-10", "variant": "explainer", "status": "failed"},
                  {"date": "2026-07-09", "variant": "short", "status": "blocked"}],
    "audio_incomplete_days": [{"date": "2026-07-10", "ready": 2}],
    "threads": {"pending": 58, "posted": 0, "blocked": 2},
    "gov_frozen": 7,
    "audio_mb": 512,
    "audio_oldest": "2026-01-01",
    "audio_retention_ok": False,
    "eval_batches": [{"run_at": "2026-07-12 22:10", "run_kind": "weekly-cron",
                      "cases": 18, "passed": 88, "failed": 4, "failed_cases": 2,
                      "failing": ["digest×inj-override", "brief×baseline"]}],
}

GREEN_PIPELINE = {
    "emb_overdue": 0,
    "failing_sources": [],
    "audio_bad": [],
    "audio_incomplete_days": [],
    "threads": {"pending": 58, "posted": 3, "blocked": 0},
    "gov_frozen": 0,
    "audio_mb": 320,
    "audio_oldest": "2026-05-16",
    "audio_retention_ok": True,
    "eval_batches": [],
}


def test_red_states_all_visible():
    html = _render(RED_PIPELINE)
    for needle in (
        "Pipeline health",
        "unembedded",                      # (1) stale embeddings
        "Failing sources",                 # (2) red source panel
        "check for a URL move first",      # (2) remediation hint
        "explainer",                       # (3) audio backlog row
        "incomplete day",                  # (3) missing-variant day
        "2 blocked",                       # (3) blocked threads
        "governance frozen-active",        # (4)
        "retention stalled",               # (5)
        "digest×inj-override",             # (6) failing eval case named
    ):
        assert needle in html, f"missing red-state marker: {needle}"


def test_green_state_has_no_red_panels():
    html = _render(GREEN_PIPELINE)
    assert "Pipeline health" in html
    assert "Failing sources" not in html
    assert "retention stalled" not in html
    assert "incomplete day" not in html
    # pending threads are annotated as non-failures (decision D1 pending)
    assert "pending ≠ failure" in html
    # no eval runs yet → the how-to hint, not an empty table
    assert "app.eval_harness" in html


def test_empty_pipeline_degrades_gracefully():
    html = _render({})
    assert "Pipeline health unavailable" in html
