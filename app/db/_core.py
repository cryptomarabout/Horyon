"""Shared Postgres infrastructure for the app.db package.

pgvector values are passed as text literals cast with ``::vector`` so no extra
adapter package is required. Domain query modules import the helpers below; the
single-statement helpers collapse the open/cursor/execute/fetch boilerplate.
"""
from __future__ import annotations

import contextlib
import logging
from psycopg2 import pool as pgpool
from psycopg2.extras import RealDictCursor

from .. import config

log = logging.getLogger("app.db")

# Lazily-created connection pool. Building it eagerly at import time would make every
# `import app.db` require a reachable Postgres — which breaks unit tests, tooling, and
# CI that only need the pure helpers. The pool is created on first real DB use instead.
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = pgpool.ThreadedConnectionPool(minconn=1, maxconn=8, dsn=config.DATABASE_URL)
    return _pool


@contextlib.contextmanager
def _conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# --------------------------------------------------------------------------- #
# Query helpers — collapse the `with _conn() ... cursor ... execute ... fetch`
# boilerplate that every single-statement accessor below would otherwise repeat.
# Each opens (and commits via `_conn`) its own transaction, so they suit one-shot
# reads/writes; multi-statement transactions still open `_conn()` explicitly.
# --------------------------------------------------------------------------- #
def _fetchall(sql: str, params=(), *, dict_rows: bool = False) -> list:
    """Run one SELECT; return all rows (dicts when ``dict_rows``, else tuples)."""
    factory = RealDictCursor if dict_rows else None
    with _conn() as conn, conn.cursor(cursor_factory=factory) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _fetchone(sql: str, params=(), *, dict_rows: bool = False):
    """Run one SELECT; return the first row (dict/tuple) or None."""
    factory = RealDictCursor if dict_rows else None
    with _conn() as conn, conn.cursor(cursor_factory=factory) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _execute(sql: str, params=()) -> int:
    """Run one write statement; return the affected rowcount."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


# --------------------------------------------------------------------------- #
# Importance scoring (see app/scoring.py) — reference data + corroboration
# --------------------------------------------------------------------------- #
def _term_boundary_regex(terms: list[str]) -> str:
    """Build a POSIX word-boundary alternation regex (\\y…\\y) for ~* matching."""
    import re as _re
    parts = [_re.escape(t) for t in terms if t and len(t) >= 3]
    return r"\y(" + "|".join(parts) + r")\y" if parts else ""
