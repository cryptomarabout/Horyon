---
name: narrative-quality
description: Narrative cluster quality — coherence gate, source diversity, momentum baseline, synthesis prompt constraints, entity resolution
metadata:
  type: project
  version: "1.0"
  updated: "2026-06-20"
  scope: app/narratives.py, app/prompts.py, app/entities.py
---

## Overview

The narrative layer (`app/narratives.py`) clusters 45-day digest bullets and podcast signals
into persistent themes carrying a momentum state. Quality failures here surface on the public
web UI (Narratives board) and can mislead the weekly digest and thread generation.

## Pipeline

```
gather_signals (bullet_analyses + podcast_episodes)
  → embed_signals (Ollama, 768-dim)
  → cluster (greedy: entity overlap primary, cosine support)
  → consolidate (merge split-batch duplicates)
  → prune_signals (cosine to centroid < MIN_SIGNAL_RELEVANCE=0.62 → drop)
  → apply_entity_cap (board diversity: max 1 narrative per SEMI_BROAD entity)
  → momentum (compute state)
  → synthesize (LLM label + thesis, capped at MAX_LLM_SYNTHESES=20)
  → replace_narratives (wipe + insert — full rebuild)
```

## Signal Coherence Gate

**`_prune_signals()`** (existing): Recomputes the cluster centroid over ALL signals, then drops
any signal whose cosine to that centroid is below `MIN_SIGNAL_RELEVANCE = 0.62`. This is the
primary guard against off-topic signals polluting a cluster.

Signals without valid embeddings (embed call failed) are kept unconditionally — entity overlap is
the stronger inclusion signal when no vector is available.

## Source Diversity Gate (added 2026-06-20)

**Location**: `_momentum(cluster, ref)` in `narratives.py`.

**Rule**: If a cluster has ≤1 distinct source domain AND fewer than 3 signals in the last 48h,
its `momentum_state` is demoted to `dormant` regardless of the computed ρ value.

This prevents a single prolific account (e.g. `@aixbt_agent`, a Tier-3 source) from generating
a "heating" narrative by posting many items about the same topic in a short burst.

```python
distinct_domains = len({_source_domain(s.get("url") or "") for s in cluster["signals"] if s.get("url")})
if distinct_domains <= 1 and delta < 3 and state not in ("dormant",):
    state = "dormant"
```

`_source_domain()` treats `nitter.net` and `x.com` as the same domain (both are Twitter).

## Minimum Cluster Gates (existing)

| Gate | Value | Purpose |
|---|---|---|
| `MIN_SIGNALS = 3` | 3 signals | Real narrative needs ≥3 corroborating signals |
| `MIN_SPAN_DAYS = 2` | 2 days | Signals must span ≥2 distinct days (drops same-day bursts) |
| `BASELINE_HOURS = 504` | 21 days | Momentum baseline window — satisfies ≥7-day requirement |

## Momentum States

```
forming  — age ≤ 72h and n ≤ 3 signals
heating  — ρ ≥ 1.5 and R (recent 48h mass) ≥ R_MIN (1.2)
steady   — default (no extreme pattern)
cooling  — ρ ≤ 0.7 (recent activity below baseline trend)
dormant  — last signal > 168h ago, OR source diversity gate triggered
```

`ρ = (R + 1) / (B + 1)` where B is baseline 48h-normalised mass over 21 days.

## Narrative Synthesis Prompt Constraints (updated 2026-06-20)

**Location**: `NARRATIVE_SYNTHESIS_SYSTEM` in `prompts.py`.

Added: "GROUNDING: use ONLY information present in the signal BODY/CONTENT blocks. Signal titles
are labels, not verified facts — always check the corresponding body before citing a claim. Do NOT
introduce entities, protocols, percentages, or statistics that do not appear in the signal bodies."

Key existing constraints that remain:
- "Do NOT invent facts not supported by the signals"
- Signals ranked by importance ★ — high-★ signals are the thesis anchors
- Entity concentration rule: >70% single-protocol cluster → specific label, not broad narrative
- `MAX_LLM_SYNTHESES = 20` — caps free-tier LLM cost per rebuild

## Entity Resolution

**`_EntityMatcher`** (class): word-boundary match of signal text vs `entity_memory`.
Uses `entities.matchable_term()` to reject generic terms (`yield`, `vault`, `protocol`).
`BROAD_ENTITIES` frozenset prevents ubiquitous protocols (aave, usdc, ethereum, circle) from
acting as super-connectors across unrelated clusters.

**`SEMI_BROAD`** frozenset: versioned protocols (aave-v4, uniswap-v3) capped at 1 narrative
via `_apply_entity_cap()` (merges smaller clusters into the largest to preserve signals).

**No alias extraction** happens in narratives — aliases are resolved during `entity_memory` upsert
at ingest time (via `app/entities.py`) and stored; the matcher reads `entity_memory.aliases`.

## Rebuild Operations

Always **full rebuild** (wipe + insert): `db.replace_narratives(final, signals_by_slug)` holds
an advisory lock during the wipe to prevent partial-read race with the web UI.

After any slug/alias change in `entity_memory`:
```bash
docker exec horyon-bot python3 -m app.narratives
docker exec horyon-bot python3 -m app.entity_graph
```

## Validation Checklist

- [ ] No narrative with `state != 'dormant'` has only 1 source domain and < 3 recent signals
- [ ] `_prune_signals` log shows dropped signal count at DEBUG level
- [ ] `NARRATIVE_SYNTHESIS_SYSTEM` contains "use ONLY information present in the signal BODY"
- [ ] Rebuilding with `--no-llm` completes without exceptions
- [ ] No narrative with fewer than `MIN_SIGNALS=3` signals exists (guaranteed by `_cluster()` filter)
- [ ] Source diversity gate log appears at DEBUG for single-source narratives
