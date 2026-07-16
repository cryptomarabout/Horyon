"""app.db.evals — scored LLM grounding eval runs (see app/eval_harness.py).

One row per path×case per run; every row in a run shares the same run_at so the
monitor can group a run into a single line. Bot-only — the web never reads this.
"""
from __future__ import annotations

import json
from datetime import datetime

from ._core import _conn, _fetchall


def insert_eval_results(run_at: "datetime", run_kind: str, rows: list[dict]) -> int:
    """Persist one eval run's rows. rows: eval_harness.run_case() dicts."""
    if not rows:
        return 0
    with _conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO eval_runs (run_at, run_kind, path, case_name, model_used,
                                   checks, passed, failed, notes)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            """,
            [(run_at, run_kind, r["path"], r["case_name"], r.get("model_used", ""),
              json.dumps(r["checks"]), r["passed"], r["failed"], r.get("notes", ""))
             for r in rows],
        )
        return len(rows)


def get_eval_batches(limit: int = 8) -> list[dict]:
    """Last N eval runs, one summary row per run (grouped by run_at)."""
    rows = _fetchall(
        """
        SELECT run_at, run_kind, count(*) AS cases,
               sum(passed) AS passed, sum(failed) AS failed,
               count(*) FILTER (WHERE failed > 0) AS failed_cases,
               COALESCE(array_agg(DISTINCT path || '×' || case_name)
                        FILTER (WHERE failed > 0), '{}') AS failing
          FROM eval_runs
         GROUP BY run_at, run_kind
         ORDER BY run_at DESC
         LIMIT %s
        """,
        (limit,),
    )
    return [
        {"run_at": r[0], "run_kind": r[1], "cases": r[2], "passed": r[3],
         "failed": r[4], "failed_cases": r[5], "failing": list(r[6] or [])}
        for r in rows
    ]
