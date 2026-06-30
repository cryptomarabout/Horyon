---
name: ingest-quality
description: Ingest pipeline quality gates — URL normalization, content thresholds, quality_flag assignment, source health escalation
metadata:
  type: project
  version: "1.0"
  updated: "2026-06-20"
  scope: app/ingest.py, app/db.py, app/scoring.py
---

## Overview

The ingest pipeline (`app/ingest.py`) fetches ~107 RSS/nitter feeds every 20 min and stores
results in `feed_items`. Several quality issues can silently degrade downstream scoring,
entity extraction, and digest generation if not gated at ingest time.

## URL Normalisation (Phase 2b)

**Problem**: CoinTelegraph URLs appear 2-3× in the corpus because the same article
is fetched with and without `?utm_source=rss&utm_medium=rss` tracking params. The
`UNIQUE(link)` constraint only catches exact matches; variants bypass it.

**Fix**: `_normalize_url(url)` in `app/ingest.py` strips UTM/ref params, normalises
scheme to `https`, and strips trailing slashes BEFORE the link dedup check.

```python
def _normalize_url(url: str) -> str:
    """Strip UTM/ref params, normalise scheme to https, strip trailing slash."""
```

Applied in `clean_items()` before `seen_links` dedup. Does NOT affect the stored `link`
value — the normalised URL IS stored, so downstream links point to canonical URLs.

## Content Quality Gate (Phase 2a/2e)

**Existing gate**: `MIN_TEXT_LEN = 40` — items with fewer than 40 chars of plain text
(after HTML stripping) are dropped in `clean_items()` before insert. This is the hard floor.

**New gate**: `quality_flag` column (see below) flags items that passed the 40-char floor
but are still too thin for reliable scoring (40–79 chars). These are mostly Blockworks
newsletter teasers (35.4% thin rate) or short tweet-only items.

## `quality_flag` Enum (Phase 1c)

Added to `feed_items` via migration. Values:

| Flag | Meaning | Downstream impact |
|---|---|---|
| `ok` | Substantive content, informative title | No penalty |
| `thin_content` | `len(plain_text) < 80` chars | Excluded from corroboration+amount signals in scoring |
| `nitter_handle_title` | RSS title starts with `@` or `https://` (bare handle/URL, not a topic) | Scoring: title-keyword signals skipped |
| `boilerplate_title` | RSS title matches newsletter/issue/section-header pattern | Scoring: title-keyword signals skipped |

**Detection** (`title_content_coherence_check(title, content)`):
1. If `len(plain_text) < 80` → `thin_content`
2. If title starts with `@` or URL → `nitter_handle_title`
3. If title matches `_BOILERPLATE_TITLE_RE` → `boilerplate_title`
4. Otherwise → `ok`

## Scoring Penalty (Phase 2c)

In `app/scoring.py → compute_importance_scores()`:

- `thin_content` items are **excluded from the corroboration and amount-text signals**
  (`s1`, `s2`, `s4`) — they still count for velocity (`s3`) since the timestamp is valid.
- The filter is applied by separating `substantive_feed` from `feed` before computing
  `credibilities`, `domains`, and `amount_text`.

```python
substantive_feed = [r for r in feed if r.get("quality_flag", "ok") != "thin_content"]
```

## Source Health Escalation (Phase 2d)

- `db.get_chronic_failing_sources(min_failures=5)` returns `(url, consecutive_failures)` rows.
- Called in `run_once()` after every ingest cycle; each chronic source gets a WARNING log.
- `insights.glassnode.com` has **1195 consecutive failures** — completely dead, should be
  removed from `app/feeds.py` or the URL should be updated.

## Schema Migration

**Must be applied before the next ingest cycle**:

```sql
ALTER TABLE feed_items ADD COLUMN IF NOT EXISTS title VARCHAR(512);
ALTER TABLE feed_items ADD COLUMN IF NOT EXISTS quality_flag VARCHAR(32) NOT NULL DEFAULT 'ok';
```

The `db.insert_feed_items()` function falls back gracefully to the legacy 7-column path if
the columns are absent (catches pre-migration runs). After migration, backfill runs
automatically on the next ingest cycle via `quality_flag DEFAULT 'ok'` — old rows stay `ok`
until re-ingested.

## Validation Checklist

- [ ] `python -m app.ingest --dry-run` completes without exceptions after code changes
- [ ] No duplicate CoinTelegraph URLs when queried: `SELECT clean_link, COUNT(*) FROM (SELECT REGEXP_REPLACE(LOWER(link), '[\?&]utm_[^&]*', '', 'g') AS clean_link FROM feed_items WHERE ingested_at > now() - interval '2 days') t GROUP BY clean_link HAVING COUNT(*) > 1`
- [ ] `insights.glassnode.com` WARNING appears in `docker compose logs -f bot`
- [ ] `quality_flag` column exists after migration: `SELECT column_name FROM information_schema.columns WHERE table_name='feed_items' AND column_name='quality_flag'`
- [ ] `blockworks.com` thin rate decreases over time as new items get `thin_content` flag
- [ ] Scoring corroboration excludes thin items: check `score_breakdown.s1` for Blockworks-only bullets
