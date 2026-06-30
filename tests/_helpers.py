"""Shared pure-test helpers (NOT collected — filename doesn't match test_*).

pytest's prepend import mode puts the tests/ directory on sys.path, so test
modules import these as a top-level module: `from _helpers import ts_ago`.
Keeping the scaffolding here de-duplicates the per-file `_ts` / `_make_ref`
copies that had drifted between test modules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def ts_ago(hours: float) -> datetime:
    """A tz-aware UTC timestamp ``hours`` in the past (fractional hours allowed)."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def make_ref_data(tvls=None, entity_terms=None, coverage=None, covered=None):
    """Build a scoring._RefData without running its DB-backed constructor.

    Mirrors the shape compute_importance_scores() assembles so the pure signal
    functions can be exercised in isolation.
    """
    from app.scoring import _RefData

    ref = _RefData.__new__(_RefData)
    ref.entity_terms = entity_terms or []
    ref.protocol_tvls = tvls or []
    ref.covered_word_sets = covered or []
    ref.recent_entity_coverage = coverage or {}
    return ref
