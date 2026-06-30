"""app.db.narratives — narrative clusters/signals + the entity co-occurrence graph."""
from __future__ import annotations

from psycopg2.extras import execute_values

from ._core import _conn, _fetchall, _vec_literal


def get_existing_narratives() -> list[dict]:
    """Existing narratives for label/thesis reuse during a rebuild."""
    return [dict(r) for r in _fetchall(
        "SELECT slug, label, thesis, key_points, watch_next, contrarian, entity_slugs, "
        "signal_count, model_used FROM narratives",
        dict_rows=True,
    )]


def replace_narratives(narratives: list[dict], signals: dict) -> int:
    """Full-rebuild: wipe narratives (cascade clears narrative_signals) + insert the new set.

    narratives: [{slug, label, thesis, entity_slugs, centroid(list|None), state,
                  intensity_48h, baseline, momentum_ratio, delta_48h, signal_count,
                  dominant_type, severity, first_seen, last_signal_at, model_used}]
    signals: {slug: [{signal_type, signal_ref, title, body, url, importance, ts}]}
    """
    with _conn() as conn, conn.cursor() as cur:
        # Serialize concurrent full rebuilds (post-digest orchestration vs the 3h cron
        # vs a manual CLI run) — without this, two overlapping DELETE+INSERT cycles on
        # the same tables deadlock or hit duplicate-slug errors. Auto-released on commit.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (91237465,))
        cur.execute("DELETE FROM narratives")
        for n in narratives:
            centroid = n.get("centroid")
            cur.execute(
                """
                INSERT INTO narratives
                    (slug, label, thesis, key_points, watch_next, contrarian, entity_slugs, centroid,
                     state, intensity_48h, baseline, momentum_ratio, delta_48h, signal_count,
                     dominant_type, severity, first_seen, last_signal_at, model_used,
                     sector, source_count, updated_at)
                VALUES (%s, %s, %s, %s::text[], %s::text[], %s, %s::text[], %s::vector, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                """,
                (n["slug"], n["label"], n.get("thesis"), n.get("key_points") or [],
                 n.get("watch_next") or [], n.get("contrarian"), n.get("entity_slugs") or [],
                 _vec_literal(centroid) if centroid else None,
                 n.get("state", "forming"), n.get("intensity_48h"), n.get("baseline"),
                 n.get("momentum_ratio"), n.get("delta_48h", 0), n.get("signal_count", 0),
                 n.get("dominant_type"), n.get("severity"), n.get("first_seen"),
                 n.get("last_signal_at"), n.get("model_used", ""),
                 n.get("sector"), n.get("source_count")),
            )
            rows = signals.get(n["slug"]) or []
            if rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO narrative_signals
                        (narrative_slug, signal_type, signal_ref, title, body, url, importance, ts)
                    VALUES %s ON CONFLICT DO NOTHING
                    """,
                    [(n["slug"], s["signal_type"], s["signal_ref"], s.get("title"),
                      s.get("body"), s.get("url"), s.get("importance"), s.get("ts"))
                     for s in rows],
                    template="(%s, %s, %s, %s, %s, %s, %s, %s::timestamptz)",
                )
    return len(narratives)


# ── Entity co-occurrence graph ────────────────────────────────────────────────
def replace_entity_edges(edges: list[dict]) -> int:
    """Full-rebuild of entity_edges: wipe + bulk insert. Caller guarantees slug_a<slug_b.
    edges: [{slug_a, slug_b, weight, last_seen, npmi, examples}]
    npmi = association strength (down-weights ubiquitous hubs); examples = up to 3
    {link, ts, snippet} JSON evidence rows behind the link."""
    import json as _json
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM entity_edges")
        if edges:
            execute_values(
                cur,
                "INSERT INTO entity_edges (slug_a, slug_b, weight, last_seen, npmi, examples) VALUES %s",
                [(e["slug_a"], e["slug_b"], int(e["weight"]), e.get("last_seen"),
                  e.get("npmi"), _json.dumps(e.get("examples") or []))
                 for e in edges],
                template="(%s, %s, %s, %s::timestamptz, %s, %s::jsonb)",
            )
    return len(edges)
