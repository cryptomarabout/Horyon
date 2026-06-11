"""Narrative intelligence layer — cluster cross-source signals into persistent
narratives carrying a momentum state (forming / heating / steady / cooling / dormant).

Pipeline (full rebuild, run post-digest + on a cron):
  1. Gather signals from three sources over a lookback window:
       news        — digest_bullet_analysis (already importance-scored 0–100)
       podcast     — podcast_episodes.analysis (notable_claims / predictions)
       governance  — governance_proposals
  2. Resolve entities per signal (word-boundary match vs entity_memory).
  3. Embed each signal (Ollama, 768-dim) and greedily cluster by
       entity overlap (primary) + embedding cosine (support).
  4. Compute momentum per cluster (mass = importance/100, windows anchored to a
       reference time — mirrors app/scoring.py).
  5. Synthesize a label + thesis + watch-next + contrarian per cluster (LLM,
       reusing unchanged clusters to bound cost).
  6. Persist via db.replace_narratives (wipe + insert — safe full rebuild).

Best-effort throughout: any failure is logged, never raised — narrative rebuild
must never break the digest that triggers it.

CLI:
  python -m app.narratives                 # rebuild now (default 14-day window)
  python -m app.narratives --days 21       # wider window
  python -m app.narratives --no-persist    # print clusters, don't write
  python -m app.narratives --no-llm        # heuristic labels only (no LLM calls)
"""
from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_t, datetime, time, timedelta, timezone

from . import config, db, embeddings, llm, prompts

log = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────
WINDOW_DAYS = 14            # signal lookback
MIN_SIGNALS = 2            # clusters below this are dropped as noise
COSINE_STRONG = 0.82       # embedding-only merge threshold
COSINE_SUPPORT = 0.68      # embedding threshold when ≥1 shared entity
MAX_LLM_SYNTHESES = 14     # cap LLM synthesis calls per rebuild
R_MIN = 1.2                # min recent mass for "heating"
DEFAULT_MASS = {"news": 0.5, "podcast": 0.55, "governance": 0.6, "market": 0.5}

# DAO governance proposals make poor narrative drivers: they're numerous, bursty
# (many proposals from one space in a short window spike velocity) and mostly
# obscure, so they flood the board with low-value governance-only clusters.
# Narratives are built from news + podcasts only. Flip to re-include them.
INCLUDE_GOVERNANCE = False

# Ubiquitous entities that must not, on their own, glue unrelated stories into one
# mega-narrative. They still count for display + embedding, just not for the
# entity-overlap merge test.
BROAD_ENTITIES = frozenset({
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "defi",
    "stablecoin", "stablecoins",
})

# ── Severity (mirrors web BulletItem.classifySeverity) ──────────────────────
_SEV_RED = re.compile(
    r"\b(hack(?:ed|s)?|exploit(?:ed|s)?|breach(?:ed|es)?|attack(?:ed|s)?|"
    r"vulnerabilit(?:y|ies)|drain(?:ed|s)?|stolen|steal|rug(?:s|ged|pull)?)\b", re.I)
_SEV_GOLD = re.compile(r"\b(governance|proposals?|vot(?:e|es|ing)|dao|upgrade[ds]?|v[34])\b", re.I)
_SEV_GREEN = re.compile(
    r"\b(launch(?:ed|es)?|deploy(?:ed|s|ment)?|yield|apy|integrat(?:ion|ions|ed|e)|partnerships?)\b", re.I)


def _severity(text: str) -> str:
    if _SEV_RED.search(text):
        return "red"
    if _SEV_GOLD.search(text):
        return "gold"
    if _SEV_GREEN.search(text):
        return "green"
    return "neutral"


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", (text or "").lower()).strip("-")


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── Entity resolution ────────────────────────────────────────────────────────
class _EntityMatcher:
    """Word-boundary matcher: signal text → set of entity slugs."""

    def __init__(self) -> None:
        # (compiled_pattern, slug)
        self.patterns: list[tuple[re.Pattern, str]] = []
        self.name_by_slug: dict[str, str] = {}
        try:
            for e in db.get_entities_for_matching():
                slug = e["slug"]
                self.name_by_slug[slug] = e["name"]
                terms = [e["name"], *(e.get("aliases") or [])]
                type_ = e.get("type")
                mentions = e.get("mention_count") or 0
                for term in terms:
                    if not term or term.startswith("@"):
                        continue
                    t = term.strip()
                    long_enough = len(t) >= 4
                    short_distinct = (
                        3 <= len(t) <= 5 and " " not in t
                        and type_ in ("protocol", "chain", "dao", "exchange", "fund")
                        and mentions >= 10
                    )
                    if not (long_enough or short_distinct):
                        continue
                    esc = re.escape(t)
                    self.patterns.append((re.compile(r"\b" + esc + r"\b", re.I), slug))
        except Exception:
            log.warning("narratives: entity matcher load failed", exc_info=True)

    def match(self, text: str) -> list[str]:
        if not text:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for pat, slug in self.patterns:
            if slug in seen:
                continue
            if pat.search(text):
                seen.add(slug)
                out.append(slug)
        return out


# ── Signal gathering ─────────────────────────────────────────────────────────
def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (t or "").lower())).strip()


def _digest_url_map(start: date_t, end: date_t) -> dict[str, str]:
    """Map normalized bullet title → source URL, parsed from stored digests."""
    out: dict[str, str] = {}
    try:
        from .digest import _parse_digest_bullets  # lazy: avoid import cycle
        rows = db.get_digests_for_range(start, end)
        for _d, content in rows:
            for b in _parse_digest_bullets(content or ""):
                if b.get("link"):
                    out[_norm_title(b["title"])] = b["link"]
    except Exception:
        log.debug("narratives: digest URL map failed", exc_info=True)
    return out


def _gather_signals(matcher: _EntityMatcher, days: int, ref: datetime) -> list[dict]:
    signals: list[dict] = []
    start = ref.date() - timedelta(days=days)

    # — News (digest bullets) —
    url_map = _digest_url_map(start, ref.date())
    try:
        for r in db.get_bullet_analyses_window(days):
            title = r["title"] or ""
            body = r.get("body") or ""
            text = f"{title} {body}".strip()
            ts = datetime.combine(r["digest_date"], time(9, 0), tzinfo=timezone.utc)
            signals.append({
                "signal_type": "news",
                "signal_ref": f"bull:{r['digest_date']}:{_slugify(title)[:48]}",
                "title": title, "body": body, "text": text,
                "url": url_map.get(_norm_title(title)),
                "importance": r.get("importance_score"),
                "ts": ts,
                "entities": matcher.match(text),
                "severity": _severity(text),
            })
    except Exception:
        log.warning("narratives: news gather failed", exc_info=True)

    # — Podcast (claims + predictions) —
    try:
        for ep in db.get_podcast_summaries_window(days):
            a = ep.get("analysis") or {}
            claims = (a.get("notable_claims") or []) + (a.get("predictions") or [])
            claims = [c for c in claims if isinstance(c, str) and c.strip()][:4]
            if not claims:
                continue
            body = " · ".join(claims)
            title = (a.get("tldr") or ep.get("title") or "").strip()[:180]
            text = f"{title} {body}".strip()
            pub = ep.get("published_at")
            ts = pub if isinstance(pub, datetime) else ref
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            signals.append({
                "signal_type": "podcast",
                "signal_ref": f"pod:{ep['video_id']}",
                "title": title or f"{ep.get('channel','Podcast')} episode",
                "body": body, "text": text,
                "url": ep.get("url"),
                "importance": None,
                "ts": ts,
                "entities": matcher.match(text),
                "severity": _severity(text),
            })
    except Exception:
        log.warning("narratives: podcast gather failed", exc_info=True)

    # — Governance — (disabled by default: see INCLUDE_GOVERNANCE)
    if INCLUDE_GOVERNANCE:
        try:
            for p in db.get_governance_signals_window(max(days, 21)):
                title = p.get("title") or ""
                space = p.get("space_name") or ""
                text = f"{space} {title}".strip()
                st = p.get("start_ts")
                ts = st if isinstance(st, datetime) else ref
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ents = matcher.match(text)
                # tie the proposal to its space's entity even if the name isn't in the text
                ents = list(dict.fromkeys(ents + matcher.match(space)))
                signals.append({
                    "signal_type": "governance",
                    "signal_ref": f"gov:{p['proposal_id']}",
                    "title": f"{space}: {title}" if space else title,
                    "body": "", "text": text,
                    "url": f"https://snapshot.org/#/{p.get('space_id','')}/proposal/{p.get('proposal_id','')}",
                    "importance": None,
                    "ts": ts,
                    "entities": ents,
                    "severity": "gold",
                })
        except Exception:
            log.warning("narratives: governance gather failed", exc_info=True)

    return signals


def _mass(sig: dict) -> float:
    imp = sig.get("importance")
    if imp is not None:
        return max(0.0, min(100.0, float(imp))) / 100.0
    return DEFAULT_MASS.get(sig["signal_type"], 0.5)


# ── Clustering ───────────────────────────────────────────────────────────────
def _embed_signals(signals: list[dict]) -> None:
    """Attach a 768-dim vector to each signal (best-effort, parallel)."""
    def _one(s: dict) -> None:
        try:
            s["vec"] = embeddings.embed(embeddings.clean_for_embedding(s["text"]) or s["text"])
        except Exception:
            s["vec"] = None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(_one, signals))
    except Exception:
        log.warning("narratives: embedding pass failed", exc_info=True)
        for s in signals:
            s.setdefault("vec", None)


def _narrow(entities: list[str]) -> set[str]:
    return {e for e in entities if e not in BROAD_ENTITIES}


def _cluster(signals: list[dict]) -> list[dict]:
    """Greedy online clustering. Entity overlap is primary; cosine supports/breaks ties."""
    # Seed around the most important signals first for stable cluster cores.
    ordered = sorted(signals, key=lambda s: (_mass(s), s["ts"]), reverse=True)
    clusters: list[dict] = []

    for s in ordered:
        s_narrow = _narrow(s["entities"])
        best, best_score = None, 0.0
        for c in clusters:
            overlap = len(s_narrow & c["narrow_entities"])
            cos = _cosine(s.get("vec"), c.get("centroid"))
            merge = (
                overlap >= 2
                or (overlap >= 1 and cos >= COSINE_SUPPORT)
                or (cos >= COSINE_STRONG)
            )
            if not merge:
                continue
            score = overlap * 1.0 + cos
            if score > best_score:
                best, best_score = c, score
        if best is None:
            best = {
                "signals": [], "centroid": None, "n_vec": 0,
                "entities": [], "narrow_entities": set(), "entity_freq": {},
            }
            clusters.append(best)
        # add signal
        best["signals"].append(s)
        for e in s["entities"]:
            best["entity_freq"][e] = best["entity_freq"].get(e, 0) + 1
        best["narrow_entities"] |= s_narrow
        # running-mean centroid
        v = s.get("vec")
        if v:
            if best["centroid"] is None:
                best["centroid"] = list(v)
                best["n_vec"] = 1
            else:
                n = best["n_vec"]
                best["centroid"] = [(c0 * n + v0) / (n + 1) for c0, v0 in zip(best["centroid"], v)]
                best["n_vec"] = n + 1
    return [c for c in clusters if len(c["signals"]) >= MIN_SIGNALS]


# ── Momentum ─────────────────────────────────────────────────────────────────
def _momentum(cluster: dict, ref: datetime) -> dict:
    sigs = cluster["signals"]
    R = 0.0
    older = 0.0
    delta = 0
    for s in sigs:
        age_h = (ref - s["ts"]).total_seconds() / 3600.0
        m = _mass(s)
        if age_h <= 48:
            R += m
            delta += 1
        elif age_h <= 168:
            older += m
    B = older / 2.5
    rho = (R + 1.0) / (B + 1.0)

    ts_all = [s["ts"] for s in sigs]
    first_ts, last_ts = min(ts_all), max(ts_all)
    age_h = (ref - first_ts).total_seconds() / 3600.0
    last_age_h = (ref - last_ts).total_seconds() / 3600.0
    n = len(sigs)

    if last_age_h > 168:
        state = "dormant"
    elif age_h <= 72 and n <= 3:
        state = "forming"
    elif rho >= 1.5 and R >= R_MIN:
        state = "heating"
    elif rho <= 0.7:
        state = "cooling"
    else:
        state = "steady"

    return {
        "intensity_48h": round(R, 3), "baseline": round(B, 3),
        "momentum_ratio": round(rho, 3), "delta_48h": delta,
        "state": state, "first_seen": first_ts.date(), "last_signal_at": last_ts,
        "signal_count": n,
    }


def _dominant_type(cluster: dict) -> str:
    tally: dict[str, float] = {}
    for s in cluster["signals"]:
        tally[s["signal_type"]] = tally.get(s["signal_type"], 0.0) + _mass(s)
    return max(tally, key=tally.get) if tally else "news"


def _top_entities(cluster: dict, limit: int = 6) -> list[str]:
    freq = cluster["entity_freq"]
    # prefer non-broad, then by frequency
    ranked = sorted(freq.items(), key=lambda kv: (kv[0] not in BROAD_ENTITIES, kv[1]), reverse=True)
    return [slug for slug, _ in ranked[:limit]]


# ── Synthesis (label + thesis) ───────────────────────────────────────────────
def _heuristic_label(entity_names: list[str], cluster: dict) -> str:
    if entity_names:
        return " · ".join(entity_names[:2])
    # fall back to the highest-mass signal's title (trimmed)
    top = max(cluster["signals"], key=_mass)
    return (top["title"] or "Emerging signal")[:48]


def _synthesize(signals: list[dict], entity_names: list[str]) -> dict:
    """LLM label + thesis + watch_next + contrarian. Returns {} on failure."""
    try:
        user = prompts.build_narrative_synthesis_user(signals, entity_names)
        content, model = llm.complete(prompts.NARRATIVE_SYNTHESIS_SYSTEM, user,
                                      max_tokens=600, temperature=0.3, json_mode=True)
        data = llm.parse_json_loose(content)
        label = (data.get("label") or "").strip()
        if not label:
            return {}
        wn = data.get("watch_next") or []
        wn = [w.strip() for w in wn if isinstance(w, str) and w.strip()][:3]
        return {
            "label": label[:80],
            "thesis": (data.get("thesis") or "").strip(),
            "watch_next": wn,
            "contrarian": (data.get("contrarian") or "").strip() or None,
            "model_used": model,
        }
    except Exception:
        log.debug("narratives: synthesis failed", exc_info=True)
        return {}


# ── Public entry point ───────────────────────────────────────────────────────
def build_and_store(days: int = WINDOW_DAYS, persist: bool = True,
                    use_llm: bool = True, ref_date: date_t | None = None) -> dict:
    """Rebuild the narrative layer. Returns stats. Never raises on inner failures."""
    ref = datetime.now(timezone.utc)
    if ref_date and ref_date < ref.date():
        ref = datetime.combine(ref_date, time(9, 0), tzinfo=timezone.utc)

    matcher = _EntityMatcher()
    signals = _gather_signals(matcher, days, ref)
    if not signals:
        log.info("narratives: no signals in window — nothing to build")
        if persist:
            try:
                db.replace_narratives([], {})
            except Exception:
                log.warning("narratives: clear failed", exc_info=True)
        return {"signals": 0, "narratives": 0}

    _embed_signals(signals)
    clusters = _cluster(signals)
    log.info("narratives: %d signals → %d candidate clusters", len(signals), len(clusters))

    # Existing narratives for label/thesis reuse (avoid re-LLM on unchanged clusters)
    existing = []
    try:
        existing = db.get_existing_narratives()
    except Exception:
        log.debug("narratives: could not load existing for reuse", exc_info=True)

    def _match_existing(entity_slugs: list[str], n: int) -> dict | None:
        es = set(entity_slugs)
        for ex in existing:
            shared = es & set(ex.get("entity_slugs") or [])
            if len(shared) >= 2 and abs((ex.get("signal_count") or 0) - n) <= 2 and ex.get("thesis"):
                return ex
        return None

    # Build narrative records; decide which need fresh synthesis.
    records: list[dict] = []
    to_synth: list[int] = []
    for c in clusters:
        mom = _momentum(c, ref)
        top_slugs = _top_entities(c)
        entity_names = [matcher.name_by_slug.get(s, s) for s in top_slugs]
        top_sig = max(c["signals"], key=_mass)
        rec = {
            "entity_slugs": top_slugs,
            "centroid": c.get("centroid"),
            "dominant_type": _dominant_type(c),
            "severity": top_sig["severity"],
            **mom,
            "_signals": c["signals"],
            "_entity_names": entity_names,
        }
        reuse = _match_existing(top_slugs, mom["signal_count"])
        if reuse:
            rec.update({
                "slug": reuse["slug"], "label": reuse["label"], "thesis": reuse["thesis"],
                "watch_next": reuse.get("watch_next") or [],
                "contrarian": reuse.get("contrarian"),
                "model_used": reuse.get("model_used") or "",
            })
        else:
            rec["label"] = _heuristic_label(entity_names, c)
            records.append(rec)
            to_synth.append(len(records) - 1)
            continue
        records.append(rec)

    # LLM synthesis for new/changed clusters (capped, parallel) — most important first.
    if use_llm and to_synth:
        to_synth.sort(key=lambda i: records[i]["intensity_48h"], reverse=True)
        capped = to_synth[:MAX_LLM_SYNTHESES]
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futs = {pool.submit(_synthesize, records[i]["_signals"],
                                    records[i]["_entity_names"]): i for i in capped}
                for fut in as_completed(futs):
                    i = futs[fut]
                    res = fut.result()
                    if res:
                        records[i].update({
                            "label": res["label"], "thesis": res["thesis"],
                            "watch_next": res["watch_next"], "contrarian": res["contrarian"],
                            "model_used": res["model_used"],
                        })
        except Exception:
            log.warning("narratives: synthesis pool failed", exc_info=True)

    # Assign stable, unique slugs.
    used: set[str] = set()
    signals_by_slug: dict[str, list[dict]] = {}
    final: list[dict] = []
    for rec in records:
        slug = rec.get("slug")
        if not slug:
            base = _slugify(rec.get("label") or "-".join(rec["entity_slugs"][:3])) or "narrative"
            slug = base[:60]
        s = slug
        k = 2
        while s in used:
            s = f"{slug}-{k}"
            k += 1
        used.add(s)
        rec["slug"] = s
        signals_by_slug[s] = [
            {"signal_type": x["signal_type"], "signal_ref": x["signal_ref"],
             "title": x["title"], "body": x.get("body"), "url": x.get("url"),
             "importance": x.get("importance"), "ts": x["ts"]}
            for x in rec.pop("_signals")
        ]
        rec.pop("_entity_names", None)
        rec.setdefault("watch_next", [])
        rec.setdefault("contrarian", None)
        rec.setdefault("thesis", None)
        rec.setdefault("model_used", "")
        final.append(rec)

    # Sort: heating/forming first, then by intensity (board order is also computed in SQL).
    state_rank = {"heating": 0, "forming": 1, "steady": 2, "cooling": 3, "dormant": 4}
    final.sort(key=lambda r: (state_rank.get(r["state"], 9), -r["intensity_48h"]))

    if persist:
        try:
            db.replace_narratives(final, signals_by_slug)
            log.info("narratives: stored %d narratives", len(final))
        except Exception:
            log.warning("narratives: persist failed", exc_info=True)

    return {"signals": len(signals), "narratives": len(final),
            "states": {st: sum(1 for r in final if r["state"] == st)
                       for st in state_rank}}


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Rebuild the narrative intelligence layer.")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS, help="signal lookback window")
    ap.add_argument("--no-persist", action="store_true", help="print clusters, don't write DB")
    ap.add_argument("--no-llm", action="store_true", help="heuristic labels only (no LLM)")
    args = ap.parse_args()

    stats = build_and_store(days=args.days, persist=not args.no_persist,
                            use_llm=not args.no_llm)
    print(json.dumps(stats, indent=2, default=str))
