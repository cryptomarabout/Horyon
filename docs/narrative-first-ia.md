# Horyon — Narrative-First IA (clickable spec)

> **Product definition.** Horyon is the *narrative intelligence layer for crypto*. It tells you
> which stories are **forming, heating, and cooling** — and shows you the cross-source evidence
> and what to do about it. News, podcasts, governance and market data are **inputs**, not destinations.

This is a clickable prototype spec for the recommended direction (Architecture 3). Every wireframe
has numbered hotspots `[n]`; the **Click map** under it maps each hotspot to a route, an inspector
state, or an action. Wire those and you have a navigable prototype.

---

## 1. Object model — the four nouns

The whole IA is built from four objects. Three already exist in the DB; one is new.

| Object | What it is | Backed by | Status |
|---|---|---|---|
| **Narrative** | A persistent cluster of signals around one thesis, carrying a **momentum state** | `analyst_notes` (seeds) + `digest_bullet_analysis` + `entity_memory` → new `narratives` / `narrative_signals` | **NEW** |
| **Signal** | A typed, scored intelligence *event* (`news · podcast · governance · market`) | `digest_bullet_analysis` (news, already 0–100 scored), `podcast_episodes.analysis`, `governance_proposals`, market/TVL breaks | exists — unify as one read-model |
| **Entity** | A protocol / chain / fund / person | `entity_memory` + `entity_intel_brief` | exists |
| **Briefing** | A time-boxed synthesis (daily / weekly memo) — an *artifact*, not nav | `crypto_digest`, `weekly_digest` | exists — demoted |

**The reframe in one line:** the old top-level nav (Daily / Weekly / Podcast / Governance) was
organized by *cadence and ingest-type*. Here, **Narrative and Entity are the nouns**; cadence is a
time-window (`24h / 7d`), and podcast/governance/news/market are **evidence-type filters**.

---

## 2. Navigation graph (the clickable map)

```
                       ┌───────────────────────────┐
                       │   ⌘K  COMMAND PALETTE      │  reaches ANY object from anywhere
                       │   entities · narratives ·  │
                       │   signals · Ask Horyon     │
                       └───────────┬───────────────┘
                                   │
   ┌──────────┬──────────────┬─────┴───────┬──────────────┬───────────┐
   │          │              │             │              │           │
┌──▼───┐  ┌───▼────┐   ┌─────▼─────┐  ┌─────▼─────┐  ┌─────▼─────┐ ┌───▼────┐
│PULSE │  │SIGNALS │   │ NARRATIVE │  │  ENTITY   │  │ BRIEFINGS │ │ WATCH  │
│  /   │  │/signals│   │ /n/[slug] │  │ /e/[slug] │  │/briefings │ │ /watch │
│(home)│  │(stream)│   │ (dossier) │  │ (dossier) │  │ (memos)   │ │(pinned)│
└──┬───┘  └───┬────┘   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘ └───┬────┘
   │ row      │ row          │ entity       │ narrative    │ bullet   │ row
   └──────────┴──────────────┴──────────────┴──────────────┴──────────┘
        every signal → its narrative + entities · every narrative → its
        signals + entities · every entity → its narratives + signals + brief

   RIGHT-RAIL INSPECTOR (persistent, contextual — never a content silo):
        default → Today's Brief · signal → Analysis · entity → Brief
        · narrative → Thesis preview · ⌘K Ask → Agent
```

Two surfaces carry the product (**Pulse** + **Signal stream**); three are drill-downs
(**Narrative**, **Entity**, **Briefing**); one is the monitor surface (**Watch**). The inspector is
omnipresent and stateful (§9). The Sidebar is **deleted** — its old sections become evidence filters
and a Briefings tab.

---

## 3. Screen — PULSE (home) · `/`

The home is **not a feed**. It is a board of live narratives ranked by momentum. This is the screen
that answers "what is this product?" in three seconds.

```
┌─ HORYON ───────────────────────────────────── ⌘K ───── ◴ 24h │ 7d ── ☆ ─ ☼ ┐
│ [1]      PULSE   Signals   Briefings   Watchlist          [3]    [4] [5][6] │
│          [2 nav tabs]                                                        │
├──────────────────────────────────────────────────────────────┬─────────────┤
│ ACTIVE NARRATIVES · ranked by momentum            ◢ heating ▾ │  INSPECTOR  │
│                                                               │             │
│ 🔥 Restaking unwind           ▲▲  +18 / 48h        ●98        │  TODAY'S    │
│    EigenLayer · Symbiotic · ETH          🔴 security          │  BRIEF      │
│ ───────────────────────────────────────────────────────────  │             │
│ 🔥 Stablecoin regulation      ▲   +9 / 48h         ●86        │  3 signals  │
│    Circle · Tether · USDC                🟡 policy            │  that move  │
│ ───────────────────────────────────────────────────────────  │  the board  │
│ 🌱 RWA credit on-chain        new +6 / 48h         ●72        │  today …    │
│    Maple · Centrifuge · BlackRock        🟢 growth            │             │
│ ───────────────────────────────────────────────────────────  │  ▸ Ask      │
│ ❄ L2 fee wars                 ▼  −4 / 48h          ●61        │    Horyon   │
│    Base · Arbitrum · Optimism                                 │   [10]      │
│ ───────────────────────────────────────────────────────────  │             │
│ ▸ steady (7)  [9]                                             │  (state     │
├──────────────────────────────────────────────────────────────┤   machine   │
│ MARKET RAIL  BTC ··· ETH ··· TOTAL3 ···  TVL $XXXb ▲   [11]   │   §9)       │
└──────────────────────────────────────────────────────────────┴─────────────┘
```

**Click map**

| # | Element | Action |
|---|---|---|
| 1 | HORYON wordmark | → `/` (Pulse) |
| 2 | Nav tabs (Pulse/Signals/Briefings/Watchlist) | → `/`, `/signals`, `/briefings`, `/watch` |
| 3 | `◴ 24h │ 7d` window toggle | re-rank board against 24h or 7d momentum (client state) |
| 4 | ☆ Watch shortcut | → `/watch` |
| 5 | ⌘K | open command palette (§8) |
| 6 | ☼/☾ theme | toggle dark/light |
| 7 | **Narrative row** (title) | → `/n/[slug]` (dossier) |
| 7a | Momentum chip `▲▲ +18/48h` (hover) | inspector → **Narrative preview** (thesis + 7d sparkline) |
| 8 | Entity chips (`EigenLayer`, `ETH`…) | → `/e/[slug]` |
| 9 | `▸ steady (7)` | expand the steady/cooling tier inline |
| 10 | Ask Horyon | inspector → **Agent** state |
| 11 | Market rail ticker | → `/e/[slug]` for that asset |

State legend on every row: `🔥 heating · 🌱 forming · ➜ steady · ❄ cooling`; arrow `▲▲/▲/➜/▼`;
`●NN` = peak signal mass; color dot = dominant `signal_type`.

---

## 4. Screen — NARRATIVE DOSSIER · `/n/[slug]`

The core screen. Thesis on top, **cross-source evidence timeline** below, AI "watch next" at the
bottom. This is where Horyon proves it synthesizes rather than aggregates.

```
┌ ‹ [1]   Restaking unwind          🔥 heating  ▲▲ +18/48h        ☆ Watch [2] ┐
│ ─────────────────────────────────────────────────────────────────────────  │
│ THESIS                                              MOMENTUM                 │
│ Slashing going live converts restaking from         ▕▁▂▄▅▇█  7d   [3]↻      │
│ passive yield into tail risk; capital is            state: heating          │
│ repricing and rotating toward… (AI · 2h ago)        confidence ●●●○         │
│                                                                             │
│ KEY ENTITIES  EigenLayer↗  Symbiotic  ether.fi  ETH        [+ watch all][4] │
│ ─────────────────────────────────────────────────────────────────────────  │
│ EVIDENCE  ◆all  📰news 8  🎙pod 3  🏛gov 2  📈mkt 5             sort ▾ [5]  │
│                                                                             │
│ ●98 🔴 EigenLayer slashing goes live          2h · 4 src       📰   [6]    │
│ ●91 🟡 Symbiotic deposit-cap vote opens       9h · snapshot    🏛   [6]    │
│ ●84 🎙 "ETH supply shock by Q3" — Bankless    6h               🎙   [6]    │
│ ●77 📈 ETH staking ratio breaks 29%           14h              📈   [6]    │
│ ▸ 13 more  [7]                                                              │
│ ─────────────────────────────────────────────────────────────────────────  │
│ WATCH NEXT   ▸ Symbiotic caps   ▸ ETH staking ratio   ▸ ether.fi TVL  [8]  │
│ CONTRARIAN   1 podcast argues slashing risk is overstated →           [9]  │
└──────────────────────────────────────────────────────────────────────────── ┘
```

**Click map**

| # | Element | Action |
|---|---|---|
| 1 | `‹` back | → previous screen (Pulse by default) |
| 2 | ☆ Watch | pin narrative to `/watch`; toggles filled/outline |
| 3 | `↻` regen | re-run thesis synthesis (LLM); optimistic spinner |
| 4 | Entity chip / `+ watch all` | → `/e/[slug]` · pin all entities |
| 5 | Evidence-type filter chips | filter the timeline by `signal_type` (no nav) |
| 6 | **Signal row** | inspector → **Analysis** state (does *not* navigate — keeps you in the narrative) |
| 7 | `▸ N more` | lazy-load older evidence |
| 8 | Watch-next item | → `/signals?q=…` pre-filtered, or pin a tracked metric |
| 9 | Contrarian link | inspector → Analysis of the dissenting signal |

**Data:** thesis = clustered synthesis of `analyst_notes` for this narrative + the top-mass signals'
`digest_bullet_analysis.analysis`. Evidence rows = `narrative_signals` joined to their source tables.
Sparkline = daily `intensity` series (§6).

---

## 5. Screen — SIGNAL STREAM · `/signals`

The unified evidence feed — **Architecture 1, demoted to a tab.** It exists for the "show me
*everything*, newest first" mode and for power filtering. Reuses today's `BulletItem`/severity bar
nearly as-is; each row gains a **narrative chip**.

```
┌ Signals   ◆All 📰 🎙 🏛 📈  │  🔴Sec 🟡Pol 🟢Growth   24h ▾   ⌕ [1] ┐
│ ──────────────────────────────────────────────────────────────────── │
│ ●98 🔴 EigenLayer slashing goes live                          📰      │
│       ◇ restaking-unwind  ·  EigenLayer  ·  4 src  ·  2h    [2][3]    │
│ ●91 🟡 Aave V4 governance vote opens                          🏛      │
│       ◇ lending-renaissance · Aave · snapshot · 9h                    │
│ ●84 🎙 "ETH supply shock by Q3" — Bankless                    🎙      │
│       ◇ restaking-unwind · ETH · 6h                                   │
│ …                                                                     │
└────────────────────────────────────────────────────────────────────── ┘
```

**Click map** — `[1]` filter/search chips (client filter), `[2]` row → inspector **Analysis**,
`[3]` `◇ narrative chip` → `/n/[slug]`, entity chip → `/e/[slug]`.

---

## 6. The narrative-momentum model (grounded in `scoring.py`)

Reuses scoring.py's exact philosophy: *mass = importance, windows anchored to a reference time,
best-effort, deterministic.* No new model calls for momentum itself.

Each **signal** `i` carries `importance_score ∈ [0,100]` and a timestamp `ts`.
Define signal **mass** `mᵢ = importance_score / 100` (fallback `0.5` when score is NULL).

For a narrative `N` at reference time `T`:

```
Recent intensity   R = Σ mᵢ   over signals with (T − tsᵢ) ≤ 48h
Baseline           B = ( Σ mᵢ over 48h < age ≤ 168h ) / 2.5     # prior 5d, scaled to a 48h window
Momentum ratio     ρ = (R + k) / (B + k)        k = 1.0  (smoothing)
Display delta       Δ = count of signals in last 48h            # the "+18 / 48h" badge
Age                = T − first_signal_ts(N)
```

**State machine** (deterministic, evaluated nightly + after each digest):

```
forming 🌱  : age ≤ 72h  AND R ≥ R_min  AND baseline insufficient (new cluster)
heating 🔥  : ρ ≥ 1.5    AND R ≥ R_min
steady  ➜  : 0.7 ≤ ρ < 1.5
cooling ❄  : ρ < 0.7     AND last_signal age ≤ 168h
dormant     : last_signal age > 168h   → drop from Pulse, still searchable
```

`R_min ≈ 1.5` mass (≈ two high-importance or three mid signals). Momentum arrow: `▲▲` ρ≥2 ·
`▲` 1.5≤ρ<2 · `➜` steady · `▼` ρ<0.7. **Board rank** = sort by `(state priority, R, ρ)` so
heating/forming float above steady above cooling. **Confidence dots** = evidence diversity:
distinct `signal_type`s present (max 4) → `●●●○`. The `7d sparkline` is the daily `R`-series.

`T = now()` for live, else end-of-digest-day — same anchoring rule as `scoring.py` so **backfill
produces identical numbers**.

---

## 7. The one new backend piece — narrative clustering

Everything above reuses existing data **except** the persistent `Narrative` object. Minimal schema:

```sql
CREATE TABLE IF NOT EXISTS narratives (
    slug           text PRIMARY KEY,
    label          text NOT NULL,
    thesis         text,
    entity_slugs   text[]      NOT NULL DEFAULT '{}',
    centroid       vector(768),                       -- mean of member-signal embeddings
    state          text        NOT NULL DEFAULT 'forming',  -- forming|heating|steady|cooling|dormant
    intensity_48h  real, baseline real, momentum_ratio real,
    first_seen     date,
    last_signal_at timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS narrative_signals (
    narrative_slug text NOT NULL REFERENCES narratives(slug) ON DELETE CASCADE,
    signal_type    text NOT NULL,                     -- news|podcast|governance|market
    signal_ref     text NOT NULL,                     -- digest_bullet_analysis.id | video_id | proposal_id
    importance     smallint, ts timestamptz,
    PRIMARY KEY (narrative_slug, signal_type, signal_ref)
);
```

**Clustering job** — extend `app/analyst.py:extract_and_persist` (already runs post-digest):

1. Embed each new signal (digest bullets aren't embedded today; embed the title+body via the same
   Ollama path used in `ingest.py`).
2. Assign to the best narrative where `cosine(centroid) ≥ τ` **or** entity overlap ≥ 2; else spawn a
   new `forming` narrative seeded from the matching `analyst_notes` theme line.
3. Recompute `R / B / ρ / state` (§6) and update `centroid`, `last_signal_at`.

Best-effort, post-digest, fail-silently — identical contract to the rest of the pipeline. Never
breaks the digest.

---

## 8. Command palette · `⌘K` (the terminal spine)

One overlay, fuzzy-ranked across all four nouns + an Ask action. This is what gives the product its
Bloomberg/Linear "summon anything" feel and removes the empty-state problem.

```
┌ ⌘K ──────────────────────────────────────────────┐
│  ▌aave___________________________________________ │
│  NARRATIVES   🔥 lending-renaissance              │ → /n/lending-renaissance
│  ENTITIES     ◢ Aave  protocol  · ☆               │ → /e/aave
│  SIGNALS      ●91 Aave V4 vote opens              │ → inspector Analysis
│  ASK          ⏎ Ask Horyon about "aave" →         │ → inspector Agent (ReAct)
└───────────────────────────────────────────────────┘
```

---

## 9. Right-rail INSPECTOR — state machine

One persistent component (today's `RightPanel`, generalized). It **never** hosts a content type; it
reflects the current selection.

```
            ┌──────────────────────────────────────────────┐
            │             DEFAULT  (nothing selected)       │
            │   "Today's Brief": top-3 board-moving signals │
            │   + 1 narrative shift + Ask box               │
            └───┬───────────┬───────────┬───────────┬───────┘
   select signal│  select entity│ hover/sel narrative│  ⌘K Ask │
            ┌───▼────┐  ┌────▼─────┐  ┌────▼──────┐  ┌──▼─────┐
            │ANALYSIS│  │  ENTITY  │  │ NARRATIVE │  │ AGENT  │
            │ bullet │  │  BRIEF   │  │  PREVIEW  │  │ ReAct  │
            │analysis│  │ entity_  │  │ thesis +  │  │ stream │
            │+ score │  │ intel_   │  │ momentum  │  │ + pin  │
            │+ src + │  │ brief +  │  │ + "Open   │  │ to     │
            │parent  │  │ TVL +    │  │  dossier" │  │ Watch  │
            │narr. → │  │ governance│ │  → /n/..  │  │        │
            └───┬────┘  └────┬─────┘  └────┬──────┘  └──┬─────┘
                └──── ESC / deselect ──────┴────────────┘  → DEFAULT
```

- **ANALYSIS** ← `digest_bullet_analysis.analysis` + `score_breakdown` + source list + a link up to
  the parent narrative.
- **ENTITY BRIEF** ← `entity_intel_brief.brief_html` (instant cache) + TVL card + live proposals.
- **NARRATIVE PREVIEW** ← thesis + momentum; CTA opens the full dossier (§4).
- **AGENT** ← the existing `/api/search` ReAct loop; results pinnable to Watch.

---

## 10. Routes & component reuse

| Route | Screen | Reuses / new |
|---|---|---|
| `/` | Pulse | **new** `NarrativeBoard`, `NarrativeRow`, `MomentumChip` |
| `/signals` | Signal stream | reuse `BulletFeed`→`SignalStream`, `BulletItem`→`SignalRow` (+ narrative chip) |
| `/n/[slug]` | Narrative dossier | **new** `NarrativeDossier`, `EvidenceTimeline` |
| `/e/[slug]` | Entity dossier | reuse `entity_intel_brief` + RightPanel TVL/governance cards |
| `/briefings`, `/briefings/[date]` | Daily/weekly memos | reuse current `/` + `/d/[date]` + `WeeklyPanel` |
| `/watch` | Watchlist | **new**; localStorage-backed pins (no auth change) |
| inspector | — | generalize `RightPanel` → `Inspector` (§9) |
| `lib/db.js` | — | add `getNarratives`, `getNarrative`, `getNarrativeSignals`, `getEntityDossier` |

**Deleted:** `Sidebar` (its weekly/podcast/governance sections become evidence-type filters + the
Briefings tab). `NavSearch` → `⌘K` palette.

---

## 11. Phasing

- **P0 — prove the IA, zero new ML.** Derive narratives from `analyst_notes` theme lines only (one
  line = one candidate narrative; dedup by entity overlap). Momentum from `digest_bullet_analysis`
  matched to entities. Ship **Pulse + Narrative dossier**, generalize the inspector, demote the
  sidebar. Validates the whole reframe on existing data.
- **P1 — real narratives.** Add the `narratives` / `narrative_signals` tables + embedding clustering
  in `analyst.py`. Momentum across all four evidence types. Entity dossier. Watchlist.
- **P2 — make it ambient.** "Ask about this narrative" agent, **state-transition alerts** (push when
  a narrative flips to 🔥), and the morning-brief email (Architecture 4 as a notification layer).

---

## 12. Why this is the category-defining call

- **It owns a word** — *narrative intelligence* is unclaimed; A1/A2/A4 are great executions of
  existing categories (feed / terminal / newsletter).
- **It weaponizes the moat, not the commodity** — anyone can render news bullets; almost no one
  synthesizes cross-source narratives *with momentum*. The synthesis was already being computed and
  shipped as a byproduct (`analyst_notes`); this promotes it to the product.
- **It fixes the IA by construction** — in a narrative-and-entity world, cadence and ingest-type can
  *only* exist as filters and evidence badges. "Where does X live?" becomes unaskable.
