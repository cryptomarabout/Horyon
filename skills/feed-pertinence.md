---
name: feed-pertinence
description: Feed signal-to-noise audit — per-source SNR query, noise thresholds, dead sources, nitter health, coverage gaps
metadata:
  type: project
  version: "1.0"
  updated: "2026-06-20"
  scope: app/feeds.py, app/db.py (source_health)
---

## Overview

The source list (`app/feeds.py`) is ~107 RSS/nitter feeds. Not all sources produce equally
useful signal. This skill documents how to audit source quality and what to do when a source
degrades below acceptable thresholds.

## Signal-to-Noise Query (run monthly)

```sql
SELECT
  SPLIT_PART(link, '/', 3)                                              AS domain,
  COUNT(*)                                                              AS items_30d,
  ROUND(AVG(CASE WHEN LENGTH(COALESCE(content,'')) < 80
                 THEN 1.0 ELSE 0.0 END) * 100, 1)                      AS pct_thin,
  ROUND(100.0 * COUNT(*) FILTER (WHERE quality_flag != 'ok')
        / NULLIF(COUNT(*), 0), 1)                                       AS pct_mismatch
FROM feed_items
WHERE ingested_at >= NOW() - INTERVAL '30 days'
GROUP BY domain
HAVING COUNT(*) > 10
ORDER BY pct_thin DESC;
```

(If `quality_flag` column does not yet exist, drop the `pct_mismatch` column from the query.)

## Noise Thresholds

| Metric | Threshold | Action |
|---|---|---|
| `pct_thin > 30%` AND `items_30d > 20` | Noise source | Flag for review; consider removing or de-weighting |
| `pct_mismatch > 30%` | Title/content mismatch | Investigate RSS feed format; add to `BOILERPLATE_TITLE_PATTERNS` if systematic |
| `consecutive_failures >= 5` | Chronic failure | WARNING in logs; check if URL changed |
| `consecutive_failures >= 100` | Dead source | Remove from `feeds.py` |

## Known Issues (as of 2026-06-20)

| Source | Issue | Recommendation |
|---|---|---|
| `insights.glassnode.com/rss/` | **1195 consecutive failures** — XML parsing error, completely dead | Remove from `feeds.py` or fix URL; adds ~1200 failure log entries per day |
| `blockworks.com` | 35.4% thin content — newsletter teasers / one-liner blurbs | Keep for now (quality_flag=thin_content handles it); consider full-article RSS if available |
| `www.bankless.com` | 14.8% thin content | Within acceptable range; monitor |

## Dead Source Removal Procedure

1. Check `source_health` for the URL: `SELECT consecutive_failures, last_error FROM source_health WHERE url = '<url>'`
2. Verify the feed URL is still valid in a browser
3. If dead for > 100 failures: remove from `app/feeds.py`
4. `docker compose build bot && docker compose up -d bot monitor`
5. Monitor that `source_health.consecutive_failures` resets to 0 on the next ingest

## Nitter Feed Health

All 90+ nitter feeds currently have `consecutive_failures = 0` (healthy). Nitter is running
stably at `nitter.net`. If multiple nitter feeds fail simultaneously (typical when the nitter
instance rotates), the `run_once()` function in `ingest.py` will emit ONE consolidated warning
listing all failing feeds — not N separate ones.

If nitter goes down permanently, replace nitter URLs with direct X.com URLs or use a different
nitter instance (`NITTER_HOST` env var is not currently supported — would need a code change to
replace `nitter.net` in `feeds.py` at runtime).

## Content Coverage (as of 2026-06-20)

**In scope** (all present in `feeds.py`):
- Ethereum / EF / L1 core (vitalik, timbeiko, ethereumfndn)
- DeFi protocols: Aave, Uniswap, Morpho, Ethena, Lido, Curve, Pendle, Hyperliquid
- Stablecoins / RWA: Circle, Ondo, Tether, Sky/MakerDAO, Maple
- Analytics: DeFiLlama, Dune, Nansen, Glassnode, Token Terminal
- Research / media: The Block, Decrypt, Bankless, CoinDesk, Blockworks, Chainalysis
- Funds: a16z, Paradigm, Multicoin (Delphi), Dragonfly, Pantera

**Out of scope** (excluded by digest prompt `HARD DISCARD`):
- ETF / futures / institutional / price / TA → prompt-level filter
- Solana / SOL / Phantom / Jupiter → prompt-level filter
- Memecoins / pump / shitcoins → prompt-level filter
- Quantum computing / consumer AI apps → prompt-level filter

**Borderline / monitor**:
- `@sui414` (Sui network) — Sui is a competitor L1, not DeFi Ethereum-focused. Low volume.
  If signal quality drops, remove.
- `@WuBlockchain` / `@WatcherGuru` — Tier-3 credibility (0.4 weight); kept for breaking news
  velocity. Their items score low but still trigger the velocity signal (s3).

**Removed 2026-06-19** (note in feeds.py):
- `cryptobriefing.com` — became a general-news/AI-slop aggregator polluting entity extraction.
  Do NOT re-add without a relevance gate.

## Validation Checklist

- [ ] `insights.glassnode.com` removed from `feeds.py` (or URL updated)
- [ ] SNR query runs cleanly (with `quality_flag` column after migration)
- [ ] No source has `consecutive_failures > 100` that is still in `feeds.py`
- [ ] Nitter feeds: all `consecutive_failures = 0` (query `source_health WHERE url ILIKE '%nitter%'`)
- [ ] `blockworks.com` items are classified `thin_content` after migration + next ingest
- [ ] Post-removal: `sources_ok / total_sources` ratio holds or improves
