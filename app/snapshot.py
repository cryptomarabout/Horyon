"""Snapshot DAO governance proposals via the public GraphQL API.

Snapshot has no native RSS; hub.snapshot.org/graphql is the official source (no auth).

Two-step fetch per cron run:
  1. _get_verified_space_ids() — queries all verified spaces, cached 24 h.
  2. fetch_and_store()        — queries active proposals across those spaces, upserts DB.

Job: fetch_and_store() called every 30 min by APScheduler.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from . import http

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://hub.snapshot.org/graphql"
TIMEOUT = 20

# In-process cache for verified space IDs (refreshed every 24 h).
_spaces_cache: list[str] = []
_spaces_fetched_at: float = 0.0
_SPACES_TTL = 86_400  # seconds

_SPACES_QUERY = """
{
  spaces(
    first: 500
    where: { verified: true }
    orderBy: "followers_count"
    orderDirection: desc
  ) {
    id
  }
}
"""

_PROPOSALS_QUERY = """
query ($spaces: [String!]!) {
  proposals(
    first: 50
    where: { space_in: $spaces, state: "active" }
    orderBy: "end"
    orderDirection: asc
  ) {
    id
    title
    state
    start
    end
    space { id name }
  }
}
"""


def _graphql(query: str, variables: dict | None = None) -> dict:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    return http.get_json(
        GRAPHQL_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=TIMEOUT,
    )


def _get_verified_space_ids() -> list[str]:
    """Return verified space IDs, fetching from Snapshot if the 24-h cache is stale."""
    global _spaces_cache, _spaces_fetched_at
    if _spaces_cache and (time.time() - _spaces_fetched_at) < _SPACES_TTL:
        return _spaces_cache
    try:
        data = _graphql(_SPACES_QUERY)
        spaces = (data.get("data") or {}).get("spaces") or []
        _spaces_cache = [s["id"] for s in spaces if s.get("id")]
        _spaces_fetched_at = time.time()
        log.info("snapshot: cached %d verified spaces", len(_spaces_cache))
    except Exception as exc:
        log.warning("snapshot: failed to fetch verified spaces: %s", exc)
    return _spaces_cache


def _ts(unix: int | None) -> datetime | None:
    if unix is None:
        return None
    return datetime.fromtimestamp(unix, tz=timezone.utc)


def fetch_and_store() -> int:
    """Fetch active proposals across all verified Snapshot spaces and upsert to DB."""
    from app.db import upsert_governance_proposals

    space_ids = _get_verified_space_ids()
    if not space_ids:
        log.warning("snapshot: no verified spaces available, skipping")
        return 0

    try:
        data = _graphql(_PROPOSALS_QUERY, {"spaces": space_ids})
    except Exception as exc:
        log.warning("snapshot: proposals fetch failed: %s", exc)
        return 0

    proposals = (data.get("data") or {}).get("proposals") or []
    if not proposals:
        log.info("snapshot: no active proposals found across %d spaces", len(space_ids))
        return 0

    rows = [
        {
            "proposal_id": p["id"],
            "space_id":    p["space"]["id"],
            "space_name":  p["space"]["name"],
            "title":       p["title"],
            "state":       p["state"],
            "start_ts":    _ts(p.get("start")),
            "end_ts":      _ts(p.get("end")),
        }
        for p in proposals
    ]

    n = upsert_governance_proposals(rows)
    log.info("snapshot: upserted %d proposals from %d verified spaces", n, len(space_ids))
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(fetch_and_store(), "proposals upserted")
