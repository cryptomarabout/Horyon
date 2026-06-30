---
name: digest-anti-hallucination
description: Digest LLM quality — prompt grounding, chain context framing, token budget, post-generation validation, retry logic, bullet analyst grounding
metadata:
  type: project
  version: "1.0"
  updated: "2026-06-20"
  scope: app/digest.py, app/prompts.py, app/scoring.py
---

## Overview

The daily digest (`app/digest.py` → `build_digest()`) calls the LLM chain with up to 200 feed
items. Multiple surfaces can admit hallucinated facts, fabricated URLs, or re-reported stale
stories. This skill documents every guard and their rationale.

## Grounding Constraints in Prompts (app/prompts.py)

### `DIGEST_SYSTEM` + `_DIGEST_RULES`
- **LINKS rule**: "use ONLY a URL that appears verbatim as the LINK: of the tweet you are
  summarizing. Never invent, guess, construct, or reuse an unrelated URL."
- **TEMPORAL ACCURACY**: "If a project is 'coming to'/'will deploy on'/'announces support for'
  a chain, or that chain is in TESTNET / 'launching soon', report it as ANNOUNCED/UPCOMING —
  never as already live, deployed, or operational."
- **HARD DISCARD list**: price, TA, ETF, regulation, Solana, memecoins, quantum, consumer AI apps.

### Chain Context Framing (added 2026-06-20)
**Location**: `build_digest_user()` digest_chain block.

**Before** (weak): `"DIGEST HISTORY — background context only (already filtered above)"`

**After** (explicit): 
```
"DIGEST HISTORY — historical background ONLY. These stories were already reported.
Do NOT re-report any item from this history as new unless today's INPUT TWEETS contain a
CONCRETE NEW DEVELOPMENT (new exploit amount confirmed, vote passed, protocol actually
launched). A follow-up tweet or ongoing discussion does NOT qualify."
```

The `⛔ ALREADY COVERED — ABSOLUTE EXCLUSION LIST` block with exact bullet titles (last 7 days)
is the primary deterministic filter. The chain history framing is the narrative-level backstop.

### Bullet Analyst System Prompt (added 2026-06-20)
**Location**: `BULLET_ANALYST_SYSTEM` in `prompts.py`.

Added: "Use ONLY information present in the headline, the summary, and the context blocks
provided. Do NOT add prices, dates, percentages, TVL figures, or statistics that are not
explicitly stated in the input."

Previously the prompt had (3) "Do NOT invent numbers, dates, launch events, or history" but
the new sentence is more specific about the source constraint and comes before the numbered
rules so it's harder to override.

## Known-Facts Anti-Hallucination Layer

`app/known_facts.py` + `app/audit.py` inject human-verified ground truth into six LLM write-paths:
- Daily digest (via `build_digest_user()`)
- Bullet analyst (via `_generate_one_analysis()`)
- Entity briefs (via `build_entity_brief_user()`)
- Analyst extraction (via `build_analyst_extraction_user()`)
- Twitter thread (via `build_thread_user()`)
- Audio briefing (via `build_briefing_user()`)

To add a new ground truth: add to `known_facts.py` — do NOT patch individual prompts.
`audit.prelaunch_warnings()` provides a generic safety net for un-curated pre-launch entities.

## Token Budget Management (Phase 3b)

`DIGEST_LIMIT = 200` items. At ~800 tokens/item conservatively, this could be 160k tokens —
above most free-tier model context limits. In practice, nitter tweet content is ~200-400 chars
each, so real token count is ~40-80k tokens.

**Current mitigation**:
- URL pre-filter (`covered_urls`) drops items already cited in recent digests before passing to LLM.
- `_format_items()` skips items with `len(text) <= 40`.
- The LLM's own attention window self-limits (items at the tail get less weight).

**No explicit ranked-trim is implemented**. If free-tier context limits become an issue:
- Use `scored = scoring.compute_importance_scores(bullets, digest_date)` result to pre-rank items
- Keep top-N by importance until token budget (configurable as `DIGEST_TOKEN_BUDGET` in config.py)
- The `quality_flag='thin_content'` filter in scoring already reduces noise items

## Post-Generation Validation (added 2026-06-20)

`validate_digest_output(content: str) -> list[str]` in `app/digest.py`:

```python
def validate_digest_output(content: str) -> list[str]:
    """Returns list of error strings. Empty = valid. First word = severity: HIGH/MEDIUM/LOW."""
```

Checks:
1. **HIGH**: digest is empty
2. **HIGH**: fewer than 5 parseable `•` bullet lines
3. **HIGH**: placeholder text matched (`N/A`, `[INSERT]`, `as of writing`, `I don't know`)
4. **MEDIUM**: raw markdown `**` found (prompt echo / wrong format)
5. **MEDIUM**: duplicate bullet titles (dedup failure)
6. **LOW**: boilerplate site-name pattern in bullet title
7. **LOW**: empty bullet body (title with no body text)

**Retry logic**: `build_digest()` retries once if any HIGH-severity error is found. On the
second attempt the LLM call is fresh (full prompt resent). The best response (by bullet count)
is kept. If retry also fails, the digest raises `ValueError`.

## Post-Filter Deterministic Backstop

`_post_filter_duplicates(html, covered_bullets)` in `digest.py`: after LLM generation, removes
any bullet whose title semantically matches a story from the last 7 days (Jaccard similarity via
`scoring.get_title_words` + `is_semantic_duplicate`). This is the deterministic backstop when the
LLM ignores the exclusion list.

## Validation Checklist

- [ ] `python -m app.digest --no-persist` produces ≥5 bullets
- [ ] No bullet contains `N/A`, `[INSERT]`, or `**`
- [ ] No bullet's `<b>` title appears in the last 7 days of `crypto_digest`
- [ ] `validate_digest_output()` returns `[]` on a clean digest
- [ ] Chain context framing in `build_digest_user()` contains "CONCRETE NEW DEVELOPMENT"
- [ ] `BULLET_ANALYST_SYSTEM` contains "Use ONLY information present in the headline"
- [ ] Retry fires when a mock HIGH error is injected (unit test)
