"""app.db.governance — Snapshot governance proposals."""
from __future__ import annotations

from ._core import _conn, _fetchall


def upsert_governance_proposals(rows: list[dict]) -> int:
    """Upsert a list of proposal dicts; returns the number of rows processed."""
    if not rows:
        return 0
    sql = """
        INSERT INTO governance_proposals
            (proposal_id, space_id, space_name, title, state, start_ts, end_ts, fetched_at)
        VALUES (%(proposal_id)s, %(space_id)s, %(space_name)s, %(title)s,
                %(state)s, %(start_ts)s, %(end_ts)s, NOW())
        ON CONFLICT (proposal_id) DO UPDATE SET
            state      = EXCLUDED.state,
            end_ts     = EXCLUDED.end_ts,
            fetched_at = EXCLUDED.fetched_at
    """
    with _conn() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, row)
    return len(rows)


def get_active_governance_proposals(limit: int = 6) -> list[dict]:
    """Return active/pending proposals ordered by end time (soonest first)."""
    sql = """
        SELECT proposal_id, space_id, space_name, title, state, start_ts, end_ts
        FROM governance_proposals
        WHERE state = 'active'
        ORDER BY end_ts ASC NULLS LAST
        LIMIT %s
    """
    return [dict(r) for r in _fetchall(sql, (limit,), dict_rows=True)]


def get_governance_signals_window(days: int = 21) -> list[dict]:
    """Governance signals: proposals seen within the window, newest first."""
    return [dict(r) for r in _fetchall(
        """
        SELECT proposal_id, space_id, space_name, title, state, start_ts, end_ts
        FROM governance_proposals
        WHERE COALESCE(start_ts, fetched_at) >= now() - make_interval(days => %s)
        ORDER BY start_ts DESC NULLS LAST
        """,
        (days,),
        dict_rows=True,
    )]
