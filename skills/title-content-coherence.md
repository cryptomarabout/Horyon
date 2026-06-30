---
name: title-content-coherence
description: Feed item quality classification — five boilerplate/mismatch patterns, quality_flag values, scoring and narrative gate rules
metadata:
  type: project
  version: "1.0"
  updated: "2026-06-20"
  scope: app/ingest.py, app/scoring.py, app/narratives.py
---

## Why This Matters

A feed item whose title bears no relation to its content body, or whose content is too thin to
carry meaning, poisons every downstream step:

- **Scoring**: keyword hits on an irrelevant/boilerplate title inflate `s5` (semantic criticality)
  and `s4` (entity weight) beyond what the content warrants.
- **Entity extraction**: entities extracted from a handle-only or newsletter-slug title are
  attributed to wrong content.
- **Digest generation**: the LLM sees a misleading title label and may synthesise a bullet from
  the title's framing rather than the body's actual claim.
- **Narrative clustering**: embeddings that mix an unrelated title and body distort cluster centroids.

## Five Root-Cause Patterns

### 1. RSS Boilerplate Title
**Pattern**: Feed emits a section header or newsletter slug as the item title.
**Example**: `"The Defiant Newsletter — Issue #214"` while body covers a specific protocol.
**Detection**: `_BOILERPLATE_TITLE_RE` — matches `newsletter #N`, `Issue #N`, `Week in …`, `| site`.
**Impact**: Entity extraction from title wasted; title-keyword scoring inflated by site-name terms.
**Flag**: `boilerplate_title`

### 2. Nitter / Twitter Handle or URL Title
**Pattern**: Nitter RSS truncates the item title to a bare `@handle` or a URL when the tweet
starts with a mention or link.
**Example**: Title = `"@0xngmi"` while body is a meaningful tweet thread.
**Detection**: `title.startswith("@") or re.match(r'^https?://', title)`
**Impact**: Same as boilerplate; additionally, a handle like `@arc` may falsely match the Arc
protocol entity.
**Flag**: `nitter_handle_title`

### 3. Podcast / YouTube Episode Title ≠ Chunk Body
**Pattern**: Map-reduce chunking: each intermediate chunk carries the episode title (e.g.
`"Bankless Ep. 412: The Future of RWA"`) while the body is a 500-word transcript slice about
a completely different topic covered in that segment.
**Impact**: Narrative clustering attaches unrelated chunks to the same narrative based on shared
episode-title embeddings.
**Mitigation**: Podcast chunks are NOT stored as `feed_items`; they're processed within
`app/podcasts.py` via map-reduce. The reduce output (tldr/claims) IS stored in `podcast_episodes`
and is already well-separated from the raw chunks. No quality_flag needed here.

### 4. Generic SEO Title from Scraped Page
**Pattern**: Some news sites emit a site-level SEO title (`"DeFi News | CryptoSlate"`) for
every article, not an article-specific title.
**Detection**: `_BOILERPLATE_TITLE_RE` — the `| site` sub-pattern catches `Title | SiteName`.
**Impact**: All articles from the domain score identically on title-based keyword signals.
**Flag**: `boilerplate_title`

### 5. Sponsored / Syndicated Content
**Pattern**: A repost carries an editorial title but the body begins with `"SPONSORED:"` or
a category tag injected by the syndicating feed.
**Mitigation**: The digest prompt has a hard `HARD DISCARD — Sponsored, ad, partnership
announcement...` rule. No quality_flag needed; the LLM filter handles it.
**Note**: If sponsored content slips through repeatedly from a specific domain, add that
domain to `FEED_CREDIBILITY` as Tier-3 (weight 0.4).

## `quality_flag` Enum

Stored in `feed_items.quality_flag` (VARCHAR 32, DEFAULT `'ok'`).

| Value | Condition | Scoring effect |
|---|---|---|
| `ok` | All checks pass | No penalty |
| `thin_content` | `len(plain_text) < 80` | Excluded from corroboration (s1) and amount (s2) signals |
| `nitter_handle_title` | Title is a bare `@handle` or URL | Title-keyword signals not applied |
| `boilerplate_title` | Title matches newsletter/section-header pattern | Title-keyword signals not applied |

## Scoring Penalty Rules (app/scoring.py)

```python
# In compute_importance_scores(), after fetching feed:
substantive_feed = [r for r in feed if r.get("quality_flag", "ok") != "thin_content"]
# Use substantive_feed for: credibilities (s1), domains, amount_text (s2), entity_weight (s4)
# Use full feed for: timestamps (s3 velocity — timestamp is still valid)
```

`boilerplate_title` and `nitter_handle_title` items remain in `substantive_feed` (they have
real content bodies). Their title-based entity/keyword signals are NOT separately suppressed at
the scoring level — entity extraction (`app/entities.py`) uses the full content body (which is
the tweet text / article body), not the RSS title, so the mislabelled title doesn't reach scoring
anyway (scoring operates on digest bullet text, not raw RSS titles).

## Narrative Coherence Gate (app/narratives.py)

`_prune_signals()` recomputes each cluster's centroid and drops any signal whose cosine
similarity to the centroid is below `MIN_SIGNAL_RELEVANCE = 0.62`. This is the primary
defence against title-body mismatch in the narrative layer.

Additional guard (added 2026-06-20): the **source diversity gate** in `_momentum()` demotes
narratives with ≤1 distinct source domain AND <3 recent signals to `dormant`, preventing a
single prolific account from manufacturing a momentum state.

## Validation Checklist

- [ ] `title_content_coherence_check("@0xngmi", "tweet text")` → `"nitter_handle_title"`
- [ ] `title_content_coherence_check("The Defiant Newsletter #214", "long body")` → `"boilerplate_title"`
- [ ] `title_content_coherence_check("", "short")` → `"thin_content"` (body < 80)
- [ ] `title_content_coherence_check("Aave deploys V4", "full article body text...")` → `"ok"`
- [ ] `quality_flag` column present after migration; all new rows classified
- [ ] Blockworks thin items show `quality_flag='thin_content'` in `feed_items`
