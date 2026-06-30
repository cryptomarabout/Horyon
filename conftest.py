# Presence of this file at the repo root puts the root on sys.path for pytest's default
# (prepend) import mode, so `from app import ...` resolves without an installed package.
#
# It also hosts the shared fixtures that several test modules need, so each one no longer
# re-rolls its own db-mocking boilerplate.
from unittest.mock import patch

import pytest


@pytest.fixture
def patch_db():
    """Factory fixture: patch ``app.<module>.db`` with canned return values.

    Usage::

        def test_x(patch_db):
            m = patch_db("scoring", get_protocol_tvls=[], get_entity_mention_map=[])
            ...                          # m is the MagicMock standing in for app.scoring.db

    Every patch started here is stopped on teardown, so a test can patch several
    modules' ``db`` handles without leaking state into the next test.
    """
    started = []

    def _apply(module: str, **returns):
        p = patch(f"app.{module}.db")
        mock = p.start()
        started.append(p)
        for name, value in returns.items():
            getattr(mock, name).return_value = value
        return mock

    yield _apply

    for p in reversed(started):
        p.stop()
