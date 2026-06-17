"""Entity co-occurrence graph precompute.

Scans recent feed items, detects which entities are mentioned in each item via a
single word-boundary alternation regex, and counts how often each PAIR of entities
co-occurs in the same item. The result is stored in `entity_edges` and read by the
web "all entities" map.

Why a precompute (not a live web query): co-occurrence is O(items × entities) — too
heavy for a request. The bot rebuilds it on a cron; the web just reads `entity_edges`.

Best-effort, never breaks anything. Reuses the SAME `entities.matchable_term` gate as
the narrative matcher, so the generic-term blocklist ("yield", "vault", …) applies here
too — no false co-occurrence edges from generic vocabulary.

CLI:
    python -m app.entity_graph [--days N] [--min-weight W] [--no-persist]
"""
from __future__ import annotations

import argparse
import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timezone

from . import config, db, entities

log = logging.getLogger(__name__)

# Tunables (env-overridable via config where present).
WINDOW_DAYS     = getattr(config, "ENTITY_GRAPH_DAYS", 45)
MIN_EDGE_WEIGHT = getattr(config, "ENTITY_GRAPH_MIN_WEIGHT", 2)   # pair must co-occur ≥ this
MIN_MENTIONS    = getattr(config, "ENTITY_GRAPH_MIN_MENTIONS", 2) # entity floor for a node
MAX_ITEM_CHARS  = 4000   # cap text scanned per item
MAX_ITEMS       = 40000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE  = re.compile(r"\s+")


def _plain(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", text or "")).strip()


def _build_matcher() -> tuple[re.Pattern | None, dict[str, str]]:
    """One big alternation regex of every matchable term → {lowered term: slug}.

    Terms are ordered longest-first so the alternation prefers "near protocol" over
    the bare "near" at a given position.
    """
    try:
        rows = db.get_entities_for_matching(min_mentions=MIN_MENTIONS)
    except Exception:
        log.warning("entity_graph: could not load entities", exc_info=True)
        return None, {}

    term_to_slug: dict[str, str] = {}
    terms: list[str] = []
    for e in rows:
        slug = e["slug"]
        type_ = e.get("type")
        mc = e.get("mention_count") or 0
        for term in [e["name"], *(e.get("aliases") or [])]:
            t = (term or "").strip()
            if not entities.matchable_term(t, type_, mc):
                continue
            low = t.lower()
            if low not in term_to_slug:
                term_to_slug[low] = slug
                terms.append(t)

    if not terms:
        return None, {}
    terms.sort(key=len, reverse=True)
    pat = re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.I)
    return pat, term_to_slug


def build_and_store(days: int = WINDOW_DAYS, persist: bool = True,
                    min_weight: int = MIN_EDGE_WEIGHT) -> dict:
    """Scan the last `days` of feed items, count entity co-occurrence, store edges."""
    pat, term_to_slug = _build_matcher()
    if not pat:
        log.info("entity_graph: no matchable entities — skipping")
        return {"items": 0, "nodes": 0, "edges": 0}

    try:
        items = db.get_feed_items_since(days, limit=MAX_ITEMS)
    except Exception:
        log.warning("entity_graph: feed fetch failed", exc_info=True)
        return {"items": 0, "nodes": 0, "edges": 0}

    pair_w: dict[tuple[str, str], int] = defaultdict(int)
    pair_last: dict[tuple[str, str], datetime] = {}
    pair_ex: dict[tuple[str, str], list] = {}     # key -> [{link, ts, snippet}] (≤3, recent)
    node_freq: dict[str, int] = defaultdict(int)
    n_docs = 0                                     # items with ≥1 entity (NPMI sample space)

    for it in items:
        full = _plain(it.get("content") or "")
        text = full[:MAX_ITEM_CHARS]
        if not text:
            continue
        slugs: set[str] = set()
        for m in pat.findall(text):
            slug = term_to_slug.get(m.lower())
            if slug:
                slugs.add(slug)
        if not slugs:
            continue
        n_docs += 1
        for s in slugs:
            node_freq[s] += 1
        if len(slugs) < 2:
            continue
        ts = it.get("ts")
        link = it.get("link")
        snippet = full[:180].strip()
        ordered = sorted(slugs)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                key = (ordered[i], ordered[j])
                pair_w[key] += 1
                if ts and (key not in pair_last or ts > pair_last[key]):
                    pair_last[key] = ts
                # Items are scanned newest-first → the first 3 we keep are the most
                # recent. Start at weight≥2 to bound memory to ~edge-count pairs.
                if pair_w[key] >= 2 and link:
                    ex = pair_ex.get(key)
                    if ex is None:
                        ex = pair_ex[key] = []
                    if len(ex) < 3:
                        ex.append({"link": link,
                                   "ts": ts.isoformat() if ts else None,
                                   "snippet": snippet})

    # NPMI(a,b) = ln( P(a,b) / (P(a)·P(b)) ) / -ln P(a,b)  ∈ [-1, 1].
    # +1 = always together, 0 = independent, <0 = avoid each other. Surfaces
    # *meaningful* associations instead of "everything co-occurs with Bitcoin".
    N = max(n_docs, 1)
    edges = []
    for (a, b), w in pair_w.items():
        if w < min_weight:
            continue
        da, db_ = node_freq.get(a, 1), node_freq.get(b, 1)
        pab = w / N
        npmi = 1.0
        if pab < 1.0 and da > 0 and db_ > 0:
            denom = -math.log(pab)
            npmi = (math.log(pab / ((da / N) * (db_ / N))) / denom) if denom > 0 else 0.0
        npmi = max(-1.0, min(1.0, npmi))
        edges.append({
            "slug_a": a, "slug_b": b, "weight": w,
            "last_seen": pair_last.get((a, b)),
            "npmi": round(npmi, 4),
            "examples": pair_ex.get((a, b)) or [],
        })

    node_slugs: set[str] = set()
    for e in edges:
        node_slugs.add(e["slug_a"])
        node_slugs.add(e["slug_b"])

    if persist:
        try:
            db.replace_entity_edges(edges)
        except Exception:
            log.warning("entity_graph: persist failed", exc_info=True)

    log.info("entity_graph: %d items scanned → %d nodes, %d edges (min_weight=%d)",
             len(items), len(node_slugs), len(edges), min_weight)
    return {"items": len(items), "nodes": len(node_slugs), "edges": len(edges)}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Rebuild the entity co-occurrence graph")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--min-weight", type=int, default=MIN_EDGE_WEIGHT)
    ap.add_argument("--no-persist", action="store_true", help="compute + print, don't write")
    args = ap.parse_args()

    res = build_and_store(days=args.days, persist=not args.no_persist, min_weight=args.min_weight)
    print(f"items={res['items']}  nodes={res['nodes']}  edges={res['edges']}")


if __name__ == "__main__":
    main()
