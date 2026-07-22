"""Stored-data integrity audit — find (and safely fix) hallucinations, overstatement,
temporal-modality errors and entity-mismatch issues that have already been WRITTEN to the DB.

This is the *retrospective* complement to ``scripts/llm_quality_audit.py`` (which re-runs the
LLM paths live). This one scans what is already stored — every digest bullet, bullet analysis,
Twitter thread, weekly report, narrative, entity intel brief, entity_memory.summary, and audio
briefing script — for:

  • MODALITY   — an upcoming/testnet/"coming to" entity described as already live/deployed.
                 Auto-scoped to (a) entities with a curated ``known_facts`` entry and
                 (b) entities whose OWN summary says they are pre-launch/testnet. So adding a
                 ``known_facts`` entry automatically extends regression coverage here.
  • OVERSTATE  — hype / editorializing language in surfaces that are supposed to be factual.
  • REASONING LEAK — a stored surface still carries the model's own planning/meta-commentary
                 about its task instead of (or mixed into) the real content — e.g. a reasoning
                 model narrating word-count/format instructions inline, past every in-pipeline
                 guard (2026-07-03 incident: nvidia/nemotron leaked into a stored + SYNTHESIZED
                 audio script). The cross-surface retrospective net under app/briefing.py's,
                 app/podcasts.py's, and app/entity_brief.py's own narrower, prompt-specific guards.
  • ENTITY     — junk aliases (punctuation/empty/generic), cross-type bare-name alias
                 collisions, and low-mention entities whose bare name/alias is common
                 prose (high-collision-risk) — all cause wrong word-boundary attribution
                 downstream.
  • THREADLINK — a URL baked into visible tweet text that isn't the appended source link.

Run inside the bot container (needs DB + app deps):
  docker exec horyon-bot python3 -m app.audit            # report only (read-only)
  docker exec horyon-bot python3 -m app.audit --fix      # apply the SAFE structural fixes
  docker exec horyon-bot python3 -m app.audit --quiet    # findings summary only

``--fix`` only does what is genuinely safe + self-healing:
  - DELETE entity_intel_brief rows for any entity with a curated known_facts entry (the cache
    is regenerated post-digest from the now-corrected, known_facts-aware prompt).
  - BLANK entity_memory.summary rows flagged by the modality check (analyst extraction rewrites
    them next time the entity appears, now under the temporal-modality rule).
  - REMOVE punctuation-only / empty / single-char junk aliases AND bare generic-word aliases
    (a GENERIC_TERMS token like "swap"/"core"/"public" — stripping it is always safe: the entity
    keeps its real name + distinctive aliases, only the false-positive-prone token goes).
It NEVER rewrites stored prose (digests / analyses / threads / weekly), NEVER deletes a whole
entity row, and NEVER folds cross-type collisions — those are reported as judgment calls (the
exact regenerate / DELETE / merge command is printed), because re-running the now-guarded pipeline
(or a human picking the correct side) is the right fix.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

from . import db, entities, known_facts, util

# --------------------------------------------------------------------------- #
# detection vocab
# --------------------------------------------------------------------------- #
# Present/past-tense assertions that a thing is ALREADY live on / deployed on a chain.
_LIVE_RE = re.compile(
    r"\b(deploys?\s+on|deployed\s+on|is\s+live|now\s+live|went\s+live|goes?\s+live|"
    r"is\s+(?:now\s+)?operational|now\s+operational|running\s+on|now\s+on|live\s+on|"
    r"in\s+production|now\s+supports?|now\s+available\s+on|has\s+launched\s+on|"
    r"expand(?:s|ed)\s+to|migrat(?:es|ed)\s+to|enforces?\s+migration|forces?\s+migration|"
    r"mandates?\s+(?:a\s+)?migration|mandatory\s+\w*\s*migration|now\s+integrated)\b",
    re.I,
)
# Qualifiers that make the same sentence an ANNOUNCEMENT / future event (→ not a contradiction).
_FUTURE_RE = re.compile(
    r"\b(will|won't|announce[sd]?|announcing|plan(?:s|ned|ning)?|intends?|expects?|expected|"
    r"coming\s+(?:to|soon)|set\s+to|ahead\s+of|prep(?:s|ping|ares?|aring)?|upcoming|"
    r"still\s+in\s+testnet|testnet|temp[\s-]?check|proposal|propose[sd]?|to\s+deploy|"
    r"day[\s-]?one|at\s+launch|at\s+mainnet\s+launch|before\s+mainnet|future\s+mainnet|"
    r"not\s+yet\s+live|soon|would\s+deploy|slated|roadmap|by\s+\w+\s+\d|pre-?launch)\b",
    re.I,
)
# Hype / editorializing that violates the factual-first voice (informational WARN).
_HYPE_RE = re.compile(
    r"\b(forced?\s+migration|mandatory\s+migration|enforces?\s+migration|is\s+dead|"
    r"killing\s+\w+|will\s+dominate|guaranteed|skyrocket\w*|to\s+the\s+moon|explod\w+|"
    r"obliterat\w+|destroy\w+|annihilat\w+|unstoppable|game-?chang\w+|"
    r"never\s+been\s+(?:more|better)|parabolic|wipes?\s+out|moon(?:ing|shot)|"
    r"next\s+\w+\s+killer)\b",  # NB: bare 100x/1000x omitted — legit as engineering metrics
    re.I,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|(?=•)|(?= — )")
_URL_RE = re.compile(r"https?://\S+")

_strip = util.plain_text


def _bullets(html: str) -> list[str]:
    return [_strip(l) for l in (html or "").splitlines() if l.strip().startswith("•")]


def _rows(sql: str, args: tuple = ()) -> list[tuple]:
    with db._conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


# --------------------------------------------------------------------------- #
# surface gathering — (surface_kind, key, text)
# --------------------------------------------------------------------------- #
def _gather_surfaces() -> list[tuple[str, str, str]]:
    s: list[tuple[str, str, str]] = []
    for d, content in _rows("SELECT date, content FROM crypto_digest "
                            "WHERE error IS NULL AND content <> '' ORDER BY date"):
        for b in _bullets(content):
            s.append(("digest", str(d), b))
    for dd, title, body, analysis in _rows(
        "SELECT digest_date, title, body, analysis FROM digest_bullet_analysis ORDER BY digest_date"
    ):
        s.append(("analysis", f"{dd} · {_strip(title)[:46]}",
                  f"{_strip(title)}. {_strip(body)} {_strip(analysis)}"))
    for dd, hook, tweets, cta in _rows("SELECT digest_date, hook, tweets, cta FROM digest_threads"):
        for i, t in enumerate(tweets or []):
            s.append(("thread", f"{dd} · tweet {i}", _strip(t.get("text", ""))))
        s.append(("thread", f"{dd} · hook", _strip(hook)))
        s.append(("thread", f"{dd} · cta", _strip(cta)))
    for ws, content in _rows("SELECT week_start, content FROM weekly_digest"):
        s.append(("weekly", str(ws), _strip(content)))
    for label, thesis, key_points, watch, contra in _rows(
        "SELECT label, thesis, array_to_string(key_points, ' '), "
        "array_to_string(watch_next, ' '), COALESCE(contrarian, '') FROM narratives"
    ):
        s.append(("narrative", _strip(label)[:46],
                  f"{_strip(thesis)} {_strip(key_points)} {_strip(watch)} {_strip(contra)}"))
    for name, brief in _rows("SELECT entity_name, brief_html FROM entity_intel_brief"):
        s.append(("brief", name, _strip(brief)))
    for slug, summ in _rows("SELECT slug, COALESCE(summary, '') FROM entity_memory WHERE summary <> ''"):
        s.append(("entity_summary", slug, _strip(summ)))
    for dd, variant, script in _rows(
        "SELECT digest_date, variant, script FROM digest_audio WHERE script <> '' ORDER BY digest_date"
    ):
        s.append(("audio", f"{dd} · {variant}", _strip(script)))
    return s


_PRELAUNCH_SUMMARY_RE = re.compile(
    r"(testnet|not yet live|upcoming|pre-?launch|will launch|coming soon|unreleased|"
    r"mainnet[^.]{0,30}(?:not|soon))", re.I)


def prelaunch_entities() -> dict[str, list[str]]:
    """slug -> trigger terms, for entities that are pre-launch.

    Union of the curated known_facts set and entities whose OWN stored summary says they are
    in testnet / upcoming / not-yet-live. The latter auto-discovers new Arc-like cases.
    PUBLIC — reused by the thread pre-publish gate + prompt warnings so there's ONE
    definition of 'what counts as pre-launch'."""
    out: dict[str, list[str]] = {}
    for slug in known_facts.KNOWN_FACTS:
        # known_facts is curated ground truth — mostly pre-launch (Arc), but it can also hold a
        # positive "this is live" correction (Base). Only the genuinely-upcoming ones gate modality;
        # an ESTABLISHED_MAINNET slug carries a fact but is NOT pre-launch.
        if slug in known_facts.ESTABLISHED_MAINNET:
            continue
        out[slug] = known_facts.TRIGGER_TERMS.get(slug, [slug])
    for slug, name, summ in _rows("SELECT slug, name, COALESCE(summary,'') FROM entity_memory "
                                  "WHERE summary <> ''"):
        # Skip entities curated as definitively-live: an established mainnet chain/protocol whose
        # summary merely mentions a testnet sub-feature (upgrade/bridge) is NOT pre-launch.
        if slug in known_facts.ESTABLISHED_MAINNET:
            continue
        if slug not in out and _PRELAUNCH_SUMMARY_RE.search(summ or ""):
            out[slug] = [name, slug.replace("-", " ")]  # name first → doubles as display label
    return out


def compile_prelaunch_patterns(prelaunch: dict | None = None) -> dict[str, "re.Pattern"]:
    """slug -> compiled word-boundary alternation over that entity's trigger terms."""
    prelaunch = prelaunch if prelaunch is not None else prelaunch_entities()
    return {
        slug: re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.I)
        for slug, terms in prelaunch.items() if terms
    }


def modality_violations(text: str, patterns: dict[str, "re.Pattern"]) -> list[tuple[str, str]]:
    """Sentence-level: return [(slug, sentence)] where a pre-launch entity is asserted as
    already-live with no future qualifier. The SHARED core for both the stored-data scan and
    the thread pre-publish gate (one regex set, one definition of a violation)."""
    hits: list[tuple[str, str]] = []
    for sent in _SENT_SPLIT.split(text or ""):
        if not _LIVE_RE.search(sent) or _FUTURE_RE.search(sent):
            continue
        for slug, pat in patterns.items():
            if pat.search(sent):
                hits.append((slug, sent.strip()[:200]))
                break
    return hits


def compile_established_self_patterns() -> dict[str, "re.Pattern"]:
    """slug -> compiled word-boundary alternation over an ESTABLISHED_MAINNET entity's
    SELF-reference terms only (never a sub-feature name — see ``ESTABLISHED_MAINNET_SELF_TERMS``
    docstring)."""
    return {
        slug: re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.I)
        for slug, terms in known_facts.ESTABLISHED_MAINNET_SELF_TERMS.items() if terms
    }


def reverse_modality_violations(text: str, patterns: dict | None = None) -> list[tuple[str, str]]:
    """Sentence-level: return [(slug, sentence)] where a hand-verified LIVE entity (the KNOWN_FACTS
    ∩ ESTABLISHED_MAINNET set — currently base, robinhood-chain) is described with future/planned
    language and no live qualifier in the same sentence.

    The mirror of ``modality_violations``: that catches a pre-launch entity called live (the Arc
    case). This catches the OPPOSITE — an already-live entity still called pre-launch/future (the
    Robinhood Chain case, 2026-07-20: 'a network Robinhood has signaled plans to expand beyond its
    brokerage model', describing a chain that had been live for three weeks with $1B+ DEX volume).
    Deliberately scoped to the tiny hand-curated self-term set (not all ESTABLISHED_MAINNET, and
    not KNOWN_FACTS' TRIGGER_TERMS) to keep false positives near zero — a live chain's OWN upcoming
    upgrade/feature is still correctly described in future tense, and must not trip this."""
    patterns = patterns if patterns is not None else compile_established_self_patterns()
    hits: list[tuple[str, str]] = []
    for sent in _SENT_SPLIT.split(text or ""):
        if not _FUTURE_RE.search(sent) or _LIVE_RE.search(sent):
            continue
        for slug, pat in patterns.items():
            if pat.search(sent):
                hits.append((slug, sent.strip()[:200]))
                break
    return hits


def prelaunch_warnings(text: str, exclude_curated: bool = True) -> list[str]:
    """Generic 'this entity is pre-launch — preserve modality' lines for any self-declared
    testnet/upcoming entity mentioned in ``text``. Curated known_facts entities are excluded
    by default (they already get a stronger, specific fact). This is the safety net for the
    NEXT Arc — an un-curated pre-launch entity nobody has added to known_facts yet."""
    pe = prelaunch_entities()
    pats = compile_prelaunch_patterns(pe)
    out: list[str] = []
    for slug, pat in pats.items():
        if exclude_curated and slug in known_facts.KNOWN_FACTS:
            continue
        if pat.search(text or ""):
            name = pe.get(slug, [slug])[0]
            out.append(
                f"{name} is recorded as pre-launch/testnet in Horyon's entity memory — "
                f"PRESERVE TEMPORAL MODALITY: report it as upcoming/announced, NEVER as live, "
                f"deployed, or operational."
            )
    return out


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_modality(surfaces) -> list[dict]:
    """Sentence-level: a pre-launch entity asserted as already live, with no future qualifier."""
    pats = compile_prelaunch_patterns()
    findings = []
    for kind, key, text in surfaces:
        for slug, sent in modality_violations(text, pats):
            findings.append({"kind": kind, "key": key, "slug": slug, "sentence": sent})
    return findings


def check_reverse_modality(surfaces) -> list[dict]:
    """Sentence-level: a hand-verified LIVE entity described as future/planned (the Robinhood
    Chain-direction mirror of ``check_modality``). Informational — the curated self-term set is
    small and new; report it for review rather than gate deploys on it until it has a track record."""
    pats = compile_established_self_patterns()
    findings = []
    for kind, key, text in surfaces:
        for slug, sent in reverse_modality_violations(text, pats):
            findings.append({"kind": kind, "key": key, "slug": slug, "sentence": sent})
    return findings


def check_overstatement(surfaces) -> list[dict]:
    out = []
    for kind, key, text in surfaces:
        if kind in ("narrative",):  # narratives intentionally carry a contrarian/thesis voice
            continue
        for m in _HYPE_RE.finditer(text):
            out.append({"kind": kind, "key": key, "phrase": m.group(0),
                        "ctx": text[max(0, m.start() - 50):m.start() + 60].strip()})
    return out


# Cross-surface reasoning-model leak (2026-07-03 incident): a stored surface still carries the
# model's own planning/meta-commentary about ITS task instead of (or mixed into) the actual
# content — nvidia/nemotron narrated its word-count/chapter-marker planning inline, past the
# <think>-tag strip AND every in-pipeline structural guard, straight into a stored + SYNTHESIZED
# audio script (app/briefing.py, app/podcasts.py, app/entity_brief.py each already carry a
# narrower, prompt-specific version of this same detector; this is the cross-surface retrospective
# net — it catches a leak that slips past those, or a future one in a surface that has none yet).
# Deliberately generic (not tied to one module's prompt vocabulary) and phrase-anchored rather than
# bare-verb to keep collision risk with genuine analysis/thesis prose low.
_REASONING_LEAK_RE = re.compile(
    r"\bas an ai (?:language model|assistant)\b|\bi'?m an ai\b|"
    r"\bthe user (?:wants|asks|is asking)\b|\bper the instructions\b|"
    r"\bfollowing the (?:format|instructions)\b|\bthe system prompt\b|"
    r"\bi need to (?:write|produce|generate|create|extract|summarize)\b|"
    r"\blet me (?:think|write|draft|extract|summarize|parse|note)\b|"
    r"\bi should (?:write|produce|generate|extract|summarize|note|parse)\b|"
    r"\bmust not invent\b|\bword count\b|\bchapter marker\b|\bmarker line\b|"
    r"\bhere is the (?:bullet|summary|analysis|brief|script|response)\b|"
    # Template echo + "we"-voice planning (2026-07-07 entity-brief incident: nemotron echoed
    # the brief prompt's own format template — "Short title … What happened. Why it matters."
    # — then planned aloud as "We need to prioritize hacks/exploits…". Neither this net nor
    # the in-pipeline guard had the phrases; both do now.)
    r"\bwe need to (?:write|produce|generate|create|extract|summarize|prioriti[sz]e|order|pick)\b|"
    r"\bprioriti[sz]e hacks\b|Short title\b|What happened\. Why it matters",
    re.IGNORECASE,
)


def check_reasoning_leak(surfaces) -> list[dict]:
    """A stored surface still carries the model's planning/meta-commentary about its OWN task
    rather than real content. One finding per contaminated surface (first match + total count —
    a leak rarely fires just once, so a high count is a strong tell)."""
    out = []
    for kind, key, text in surfaces:
        matches = list(_REASONING_LEAK_RE.finditer(text or ""))
        if not matches:
            continue
        m = matches[0]
        out.append({"kind": kind, "key": key, "phrase": m.group(0), "count": len(matches),
                    "ctx": text[max(0, m.start() - 50):m.start() + 80].strip()})
    return out


def check_thread_links(_surfaces) -> list[dict]:
    out = []
    for dd, tweets in _rows("SELECT digest_date, tweets FROM digest_threads"):
        for i, t in enumerate(tweets or []):
            link = (t.get("link") or "").split("#")[0]
            for u in _URL_RE.findall(t.get("text", "")):
                u0 = u.rstrip(".,)#").split("#")[0]
                if link and u0 not in link and link not in u0:
                    out.append({"key": f"{dd} · tweet {i}", "url": u, "link": t.get("link")})
    return out


def _kept_slugs() -> set:
    """Slugs a human has confirmed as real projects (entity_review verdict='keep').
    Queried fresh each time — the bot process is long-lived and verdicts are recorded
    from a separate CLI process, so caching here would go stale."""
    return {s for s, v in db.get_entity_reviews().items() if v == "keep"}


def check_entity_hygiene():
    """Returns (junk, generic, generic_name, cross_type, version_collisions_count)."""
    rows = _rows("SELECT slug, name, type, aliases, COALESCE(mention_count,0) FROM entity_memory")
    canon_by_name = defaultdict(list)  # lowercase canonical name -> [(slug, type)]
    for slug, name, typ, _al, _mc in rows:
        canon_by_name[(name or "").lower().strip()].append((slug, typ))

    junk, generic, generic_name = [], [], []
    kept = _kept_slugs()
    alias_owners = defaultdict(set)  # alias -> {(slug, type)}
    for slug, name, typ, aliases, mc in rows:
        # an entity whose canonical NAME is itself a generic word (e.g. "Gas"/"Bridge"/"Oracle")
        # is extraction noise — stripping its alias still leaves a generically-named husk, so it's
        # a DELETE candidate, not an alias-strip. Gate tightly so REAL projects that happen to be
        # named a generic word (Across→acx, Movement→move, Push→"push protocol") are NOT flagged:
        # require low mention_count AND no DISTINCTIVE alias (a ticker / sub-name proves it's real).
        nm = (name or "").lower().strip()
        if nm in entities.GENERIC_TERMS and mc <= 2 and slug not in kept:
            distinctive = [a for a in (aliases or [])
                           if (a or "").lower().strip() not in entities.GENERIC_TERMS
                           and (a or "").lower().strip() not in ("", nm)]
            if not distinctive:
                generic_name.append((slug, name, mc))
        for a in aliases or []:
            al = (a or "").lower().strip()
            if not al or len(re.sub(r"[^a-z0-9]", "", al)) <= 1:
                junk.append((slug, a))
            elif al in entities.GENERIC_TERMS:
                generic.append((slug, a))
            alias_owners[al].add((slug, typ))

    # cross-type collision: an alias equals ANOTHER entity's canonical name of a different type
    cross_type, version_coll = [], 0
    for al, owners in alias_owners.items():
        if len(owners) < 2:
            continue
        types = {t for _s, t in owners}
        if len(types) > 1 and al in canon_by_name:
            cross_type.append((al, sorted(owners)))
        else:
            version_coll += 1
    return junk, generic, generic_name, cross_type, version_coll


# --------------------------------------------------------------------------- #
# Collision risk: a low-mention entity whose bare name/alias is common prose
# --------------------------------------------------------------------------- #
_WORD_RE = re.compile(r"[A-Za-z]+")
_COLLISION_MIN_HITS = 40   # the word must be genuinely common, not a coincidence
_COLLISION_RATIO = 20      # …and dwarf the entity's own mention_count by this multiple


def _is_collision_risk(mention_count: int, feed_hits: int) -> bool:
    """A word-boundary term that fires far more often as ordinary prose than as
    genuine coverage of the entity that happens to hold it as a name/alias."""
    return feed_hits >= _COLLISION_MIN_HITS and feed_hits >= _COLLISION_RATIO * max(mention_count, 1)


def check_collision_risk() -> list[tuple[str, str, int, int]]:
    """Entities whose bare matchable name/alias is common English/finance vocabulary,
    not a real signal of coverage — the 'firm' class of bug: Inverse Finance's FiRM
    (slug 'firm', mc=1, name/alias 'firm') collided with ordinary prose 329x in a few
    weeks of feed content ("a crypto firm", "an investment firm"...) despite never
    being genuinely covered under that slug.

    `_AMBIG_SINGLE_RE` (entities.py) only forces case-sensitive matching for pure
    Titlecase/ALL-CAPS names, so a mixed-internal-case brand like 'FiRM' falls into
    the case-INSENSITIVE map — and broadening that regex turns out to catch hundreds
    of ordinary CamelCase brand names too (JustLend, PumpSwap, ZeroLend, ...), so it's
    not a safe general fix. A pre-emptive GENERIC_TERMS block isn't right either — it
    would also kill the term when it's correctly used AS the brand (the CASE-is-the-
    signal convention in docs/conventions.md). So this surfaces the mismatch for human
    review instead of blocking it outright — same review-only posture as
    generically-NAMED junk entities: a legitimate but young/rare project can have low
    mention_count too, so this is never auto-deleted.
    """
    rows = _rows(
        "SELECT slug, name, type, aliases, COALESCE(mention_count,0) FROM entity_memory "
        "WHERE mention_count <= 2"
    )
    # Decision memory: a slug with an entity_review verdict is settled — 'keep' was
    # human-confirmed real (stop re-flagging it every run), 'block' is already gone.
    reviewed = set(db.get_entity_reviews())
    candidates: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for slug, name, typ, aliases, mc in rows:
        if slug in reviewed:
            continue
        terms = {(name or "").strip()} | {(a or "").strip() for a in (aliases or [])}
        for t in terms:
            if not t or " " in t or not t.isalpha() or not (3 <= len(t) <= 8):
                continue
            if not entities.matchable_term(t, typ, mc):
                continue
            candidates[t.lower()].append((slug, mc))
    if not candidates:
        return []

    counts: dict[str, int] = defaultdict(int)
    for (content,) in _rows("SELECT content FROM feed_items"):
        for w in _WORD_RE.findall(_strip(content or "")):
            wl = w.lower()
            if wl in candidates:
                counts[wl] += 1

    hits = [
        (slug, term, mc, counts.get(term, 0))
        for term, holders in candidates.items()
        for slug, mc in holders
        if _is_collision_risk(mc, counts.get(term, 0))
    ]
    hits.sort(key=lambda x: -x[3])
    return hits


# --------------------------------------------------------------------------- #
# fixes (safe, structural, self-healing only)
# --------------------------------------------------------------------------- #
def apply_fixes(modality, junk, generic) -> dict:
    counts = {"briefs_deleted": 0, "summaries_blanked": 0, "aliases_cleaned": 0}

    # 1) drop cached briefs for any curated known_facts entity (regenerate clean post-digest)
    kf_terms = [t for terms in known_facts.TRIGGER_TERMS.values() for t in terms]
    with db._conn() as c, c.cursor() as cur:
        for name, brief in _rows("SELECT entity_name, brief_html FROM entity_intel_brief"):
            low = f"{name} {_strip(brief)}".lower()
            if any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", low) for t in kf_terms):
                cur.execute("DELETE FROM entity_intel_brief WHERE entity_name = %s", (name,))
                counts["briefs_deleted"] += 1

        # 2) blank entity summaries flagged by the modality check (analyst rewrites them)
        bad_summary_slugs = {f["slug"] for f in modality if f["kind"] == "entity_summary"}
        # also blank any summary whose own text trips a live contradiction directly
        for f in modality:
            if f["kind"] == "entity_summary":
                bad_summary_slugs.add(f["key"])
        for slug in sorted(bad_summary_slugs):
            if slug in known_facts.KNOWN_FACTS:
                continue  # keep curated-correct summaries (e.g. arc itself)
            cur.execute("UPDATE entity_memory SET summary = '' WHERE slug = %s", (slug,))
            counts["summaries_blanked"] += cur.rowcount

        # 3) remove junk + bare generic-word aliases (both are false-positive-prone tokens;
        #    array_remove leaves the entity's real name + distinctive aliases intact)
        for slug, alias in junk + generic:
            cur.execute("UPDATE entity_memory SET aliases = array_remove(aliases, %s) WHERE slug = %s",
                        (alias, slug))
            counts["aliases_cleaned"] += cur.rowcount
    return counts


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def run(do_fix: bool = False, quiet: bool = False) -> int:
    surfaces = _gather_surfaces()
    modality = check_modality(surfaces)
    reverse_modality = check_reverse_modality(surfaces)
    overstate = check_overstatement(surfaces)
    leaks = check_reasoning_leak(surfaces)
    threadlinks = check_thread_links(surfaces)
    junk, generic, generic_name, cross_type, version_coll = check_entity_hygiene()
    collisions = check_collision_risk()

    P = (lambda *a: None) if quiet else print
    P(f"\nHORYON DATA AUDIT — scanned {len(surfaces)} text surfaces\n" + "=" * 70)

    P(f"\n[MODALITY] pre-launch/testnet entity described as LIVE — {len(modality)} hit(s)")
    by_kind = defaultdict(list)
    for f in modality:
        by_kind[f["kind"]].append(f)
    for kind in ("digest", "analysis", "thread", "weekly", "narrative", "brief", "entity_summary",
                "audio"):
        for f in by_kind.get(kind, []):
            P(f"  ❌ [{kind}] {f['key']}  (entity={f['slug']})")
            P(f"        {f['sentence']}")

    P(f"\n[REVERSE MODALITY] hand-verified live entity described as future/planned — "
      f"{len(reverse_modality)} hit(s)")
    for f in reverse_modality:
        P(f"  ⚠️  [{f['kind']}] {f['key']}  (entity={f['slug']})")
        P(f"        {f['sentence']}")

    P(f"\n[OVERSTATE] hype / editorializing language — {len(overstate)} hit(s)")
    for f in overstate[:25]:
        P(f"  ⚠️  [{f['kind']}] {f['key']}  «{f['phrase']}» … {f['ctx']}")
    if len(overstate) > 25:
        P(f"     … +{len(overstate) - 25} more")

    P(f"\n[REASONING LEAK] stored surface still carries model planning/meta-commentary — "
      f"{len(leaks)} hit(s)")
    for f in leaks[:25]:
        P(f"  ❌ [{f['kind']}] {f['key']}  «{f['phrase']}» (×{f['count']}) … {f['ctx']}")
    if len(leaks) > 25:
        P(f"     … +{len(leaks) - 25} more")

    P(f"\n[THREADLINK] inline URL ≠ source link in tweet text — {len(threadlinks)} hit(s)")
    for f in threadlinks:
        P(f"  ❌ {f['key']}  {f['url']}  (link={f['link']})")

    P("\n[ENTITY] hygiene")
    P(f"  junk aliases (punctuation/empty/1-char): {len(junk)}  → --fix removes")
    for slug, a in junk[:12]:
        P(f"      {slug}: «{a}»")
    if len(junk) > 12:
        P(f"      … +{len(junk) - 12} more")
    P(f"  generic-word aliases (false-positive risk in web tag matcher): {len(generic)}  → --fix strips")
    for slug, a in generic[:12]:
        P(f"      {slug}: «{a}»")
    if len(generic) > 12:
        P(f"      … +{len(generic) - 12} more")
    P(f"  generically-NAMED junk entities (DELETE candidates — name is itself a generic word): "
      f"{len(generic_name)}  → review + DELETE")
    for slug, name, mc in generic_name[:12]:
        P(f"      {slug}: name={name!r} mc={mc}")
    if len(generic_name) > 12:
        P(f"      … +{len(generic_name) - 12} more")
    if generic_name:
        slugs = ",".join(f"'{s}'" for s, _n, _m in generic_name[:12])
        P(f"      → delete the noise rows, e.g.: DELETE FROM entity_memory WHERE slug IN ({slugs});")
    P(f"  high-collision-risk entities (bare term is common prose, not real coverage): "
      f"{len(collisions)}  → review")
    for slug, term, mc, n in collisions[:12]:
        P(f"      {slug}: term={term!r} mc={mc} vs {n} raw feed hits")
    if len(collisions) > 12:
        P(f"      … +{len(collisions) - 12} more")
    P(f"  cross-type bare-name alias collisions (wrong attribution risk): {len(cross_type)}  → review")
    for al, owners in cross_type[:15]:
        P(f"      «{al}» → " + ", ".join(f"{s}({t})" for s, t in owners))
    P("      ↳ entity_audit merge SKIPS cross-type (same-type guard). For a same-NAME cross-type")
    P("        dup, DELETE the lower-mention row by hand; leave token/chain/protocol FACETS alone.")
    P(f"  version-variant collisions (expected; fold via entity_audit merge): {version_coll}")

    # prose surfaces that need regeneration (not auto-fixable) — modality + reasoning-leak hits
    # both mean the stored text itself is wrong, never a hand-edit, only a regenerate.
    regen_flagged = modality + leaks
    prose = sorted({(f["kind"], f["key"].split(" · ")[0]) for f in regen_flagged
                    if f["kind"] in ("digest", "analysis", "thread", "weekly")})
    if prose:
        P("\n[ACTION] prose surfaces flagged — regenerate (do NOT hand-edit):")
        dates = sorted({k for _kind, k in prose})
        for d in dates:
            P(f"  • {d}:  python3 -m app.digest --regen-analyses --from-date {d}  &&  "
              f"python3 -m app.threads --date {d}")

    audio_flagged = sorted({f["key"] for f in regen_flagged if f["kind"] == "audio"})
    if audio_flagged:
        P("\n[ACTION] audio scripts flagged — regenerate (do NOT hand-edit):")
        for key in audio_flagged:
            d, _, variant = key.partition(" · ")
            P(f"  • {d} ({variant}):  python3 -m app.briefing --date {d} --variant {variant}")

    if do_fix:
        P("\n[FIX] applying safe structural fixes …")
        counts = apply_fixes(modality, junk, generic)
        P(f"  briefs deleted (regenerate post-digest): {counts['briefs_deleted']}")
        P(f"  entity summaries blanked (analyst rewrites): {counts['summaries_blanked']}")
        P(f"  junk + generic aliases removed: {counts['aliases_cleaned']}")
        if generic_name:
            P(f"  ⚠ {len(generic_name)} generically-named junk entities NOT auto-deleted "
              f"(review the DELETE above)")
        if collisions:
            P(f"  ⚠ {len(collisions)} high-collision-risk entities NOT auto-deleted (review above)")
    else:
        P("\n(run with --fix to delete stale briefs, blank bad summaries, strip junk+generic aliases)")

    total = len(modality) + len(threadlinks) + len(leaks)
    P("\n" + "=" * 70)
    P(f"SUMMARY: {len(modality)} modality · {len(reverse_modality)} reverse-modality · "
      f"{len(overstate)} overstatement · "
      f"{len(leaks)} reasoning-leak · {len(threadlinks)} threadlink · {len(junk)} junk-alias · "
      f"{len(cross_type)} cross-type · {len(collisions)} collision-risk")
    return total


if __name__ == "__main__":
    args = set(sys.argv[1:])
    code = run(do_fix="--fix" in args, quiet="--quiet" in args)
    # non-zero exit when hard issues remain (modality/threadlink), so a skill/CI can react
    sys.exit(1 if code and "--fix" not in args else 0)
