"""LLM quality / grounding audit — read-only, no DB writes.

Runs each major LLM path, captures the EXACT prompt sent + the model output, and checks
programmatically for:
  - prompt-injection completeness: are the context blocks actually populated / relevant?
  - hallucinated data: does the output cite URLs / numbers not present in the prompt input?
  - missing data: are expected ground-truth facts absent from the prompt?

Run inside the bot container (has deps + DB + LLM):
  docker exec horyon-bot python3 -m scripts.llm_quality_audit          # all tests
  docker exec horyon-bot python3 -m scripts.llm_quality_audit digest   # one test

This makes real LLM calls (a handful). It writes nothing. Output is a structured report
a human (or Claude) reads to judge analysis quality.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

# ── helpers ──────────────────────────────────────────────────────────────────
# URL identity primitives live in app/eval_harness.py (the scored T10 harness) —
# ONE definition of "this cited link is the same source" for both tools.
from app.eval_harness import URL_RE, HREF_RE, url_key as _url_key, url_keys as _url_keys

# money like $62M, $1.2B, $500,000 ; percents like +12%, -3.4% ; versions like v3, V4
MONEY_RE = re.compile(r'\$\s?\d[\d,.]*\s?(?:[bmkt]|billion|million|thousand|trillion)?\b', re.I)
PCT_RE = re.compile(r'[-+]?\d[\d.]*\s?%')
VER_RE = re.compile(r'\bv\d+(?:\.\d+)?\b', re.I)


def _norm_num(tok: str) -> str:
    return re.sub(r'[\s,]', '', tok).lower().rstrip('.')


def hr(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def mark(ok: bool, warn: bool = False) -> str:
    return "⚠️  WARN" if warn else ("✅ PASS" if ok else "❌ FAIL")


# ── Test 1: Daily digest ─────────────────────────────────────────────────────
def test_digest() -> None:
    hr("TEST 1 — DAILY DIGEST: prompt completeness + URL grounding")
    from app import digest, llm

    cap = {}
    orig = llm.complete

    def wrapped(system, user, **kw):
        out = orig(system, user, **kw)
        # capture the FIRST (and usually only) digest generation call
        cap.setdefault("system", system)
        cap.setdefault("user", user)
        cap.setdefault("output", out[0])
        cap.setdefault("model", out[1])
        return out

    llm.complete = wrapped
    try:
        html, body, model = digest.build_digest()
    finally:
        llm.complete = orig

    user = cap.get("user", "")
    out = cap.get("output", "")

    # — Prompt completeness: which context blocks are present + their sizes —
    blocks = {
        "MARKET CONTEXT (TVL)": "MARKET CONTEXT",
        "ENTITY CONTEXT": "ENTITY CONTEXT",
        "ANALYST NOTES": "ANALYST NOTES",
        "PODCAST INTELLIGENCE": "PODCAST INTELLIGENCE",
        "ALREADY COVERED (dedup)": "ALREADY COVERED",
        "DIGEST HISTORY": "DIGEST HISTORY",
        "PREVIOUS DIGEST (A merge)": "PREVIOUS DIGEST (A)",
    }
    print("\nPrompt context blocks injected:")
    for label, needle in blocks.items():
        present = needle in user
        print(f"  {mark(present, warn=not present)}  {label}")
    n_tweets = user.count("TYPE:")
    n_links_in = len(set(URL_RE.findall(user)))
    print(f"\n  input tweets (TYPE: blocks): {n_tweets}")
    print(f"  distinct URLs available in prompt: {n_links_in}")
    print(f"  prompt size: {len(user):,} chars   model: {cap.get('model')}")

    # — Output grounding: every cited URL must trace to a source link in the prompt —
    out_urls = HREF_RE.findall(out)
    prompt_keys = _url_keys(user)
    grounded = [u for u in out_urls if _url_key(u) in prompt_keys]
    halluc = [u for u in out_urls if _url_key(u) not in prompt_keys]
    n_bullets = len([l for l in out.splitlines() if l.strip().startswith("•")])
    print(f"\nOutput: {n_bullets} bullets, {len(out_urls)} links")
    print(f"  {mark(not halluc)}  URL grounding: {len(grounded)}/{len(out_urls)} cited URLs found verbatim in prompt")
    for u in halluc:
        print(f"      ❌ HALLUCINATED / not-in-prompt URL: {u}")

    # — Dedup quality: any output title semantically matching a covered story? —
    try:
        from app import scoring, db
        rows = db.get_digest_contents_for_dedup(days=7)
        _, covered = digest._build_dedup_context(rows)
        cov_sets = [scoring.get_title_words(c["title"]) for c in covered]
        cov_sets = [w for w in cov_sets if w]
        dupes = []
        for l in out.splitlines():
            if not l.strip().startswith("•"):
                continue
            m = re.search(r"<b>([\s\S]*?)</b>", l)
            if not m:
                continue
            t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            w = scoring.get_title_words(t)
            if w and any(scoring.is_semantic_duplicate(w, cw) for cw in cov_sets):
                dupes.append(t)
        print(f"  {mark(not dupes)}  dedup: {len(dupes)} output bullet(s) semantically match a story from the last 7 days")
        for d in dupes:
            print(f"      ⚠️  possible repeat: {d}")
    except Exception as e:
        print(f"  (dedup check skipped: {e})")


# ── Test 2: Bullet analysis ──────────────────────────────────────────────────
def test_bullet() -> None:
    hr("TEST 2 — BULLET ANALYSIS: number/fact grounding vs VERIFIED block")
    from app import digest, db, llm

    # pull a few real, recent bullets that actually mention known entities
    rows = db.get_digest_contents_for_dedup(days=3)
    bullets = []
    for _d, content in rows:
        bullets.extend(digest._parse_digest_bullets(content or ""))
        if len(bullets) >= 25:
            break
    from app import entities
    # prefer bullets whose entity actually HAS TVL/governance data, so the VERIFIED block
    # is genuinely exercised (CodeXero/MoneyGram etc. resolve to entities but have no TVL).
    picked, fallback = [], []
    for b in bullets:
        slugs = entities.detect_entities_in_text(f"{b['title']} {b.get('body','')}")
        if not slugs:
            continue
        fallback.append(b)
        try:
            if any(r.get("tvl_usd") for r in db.get_protocols_by_slugs(slugs)):
                picked.append(b)
        except Exception:
            pass
        if len(picked) >= 3:
            break
    for b in fallback:
        if len(picked) >= 3:
            break
        if b not in picked:
            picked.append(b)
    if not picked:
        picked = bullets[:3]

    cap = {}
    orig = llm.complete

    def wrapped(system, user, **kw):
        cap["user"] = user
        return orig(system, user, **kw)

    for b in picked:
        cap.clear()
        llm.complete = wrapped
        try:
            res = digest._generate_one_analysis(b)
        finally:
            llm.complete = orig
        analysis = res["analysis"]
        prompt = cap.get("user", "")
        verified = ""
        if "VERIFIED DATABASE FACTS" in prompt:
            verified = prompt.split("VERIFIED DATABASE FACTS", 1)[1].split("Headline:", 1)[0]
        has_notes = "PRIOR ANALYST NOTES" in prompt
        # numbers in the OUTPUT that are NOT present anywhere in the prompt = suspect
        src = prompt  # headline+body+verified+notes are all in the prompt
        src_norm = _norm_num(src)
        out_nums = MONEY_RE.findall(analysis) + PCT_RE.findall(analysis)
        suspect = [n for n in out_nums if _norm_num(n) not in src_norm]

        print(f"\n• {b['title'][:70]}")
        print(f"    VERIFIED block present: {'yes' if verified.strip() else 'NO'} | prior-notes block: {'yes' if has_notes else 'no'}")
        if verified.strip():
            print("    verified facts:" + verified.strip().replace("\n", " ")[:160])
        print(f"    {mark(not suspect)}  numbers in analysis grounded in prompt: "
              f"{len(out_nums) - len(suspect)}/{len(out_nums)}")
        for n in suspect:
            print(f"        ❌ ungrounded figure in output: {n!r}")
        print(f"    analysis: {analysis[:240]}")


# ── Test 3: Agent / search ───────────────────────────────────────────────────
def test_agent(keyword: str = "aave") -> None:
    hr(f"TEST 3 — AGENT/SEARCH ('{keyword}'): retrieval pertinence + link grounding")
    from app import specialized, db

    # capture what search_feed actually fed the model
    fed = []
    orig_impl = specialized._search_feed_impl

    def wrapped(ti):
        out = orig_impl(ti)
        fed.append({"kw": (ti or {}).get("keyword", ""), "out": out})
        return out

    specialized._search_feed_impl = wrapped
    # rebuild tool map so the wrapped impl is used
    try:
        answer = specialized.run_specialized(keyword, chat_id="audit-test")
    finally:
        specialized._search_feed_impl = orig_impl

    # retrieval pertinence: of the rows fed back, how many mention the keyword?
    all_fed_text = "\n".join(f["out"] for f in fed)
    fed_keys = _url_keys(all_fed_text)
    print(f"\n  search_feed calls: {len(fed)}  ({', '.join(f['kw'] for f in fed)})")
    print(f"  distinct source links returned to model: {len(fed_keys)}")

    out_urls = HREF_RE.findall(answer)
    halluc = [u for u in out_urls if _url_key(u) not in fed_keys]
    print(f"  answer: {len([l for l in answer.splitlines() if l.strip().startswith('•')])} bullets, {len(out_urls)} links")
    print(f"  {mark(not halluc)}  link grounding: {len(out_urls) - len(halluc)}/{len(out_urls)} answer links came from search results")
    for u in halluc:
        print(f"      ❌ link not in any search result: {u}")
    print("\n  --- answer ---\n  " + answer.replace("\n", "\n  ")[:900])


# ── Test 4: Analyst extraction ───────────────────────────────────────────────
def test_analyst() -> None:
    hr("TEST 4 — ANALYST EXTRACTION: entity_updates grounded in digest text (no invented numbers)")
    from app import db, llm, prompts

    rows = db.get_recent_digests_text(days=2)
    if not rows:
        print("  (no recent digest to test)")
        return
    _d, content = rows[0]
    import re as _re
    plain = _re.sub(r"<[^>]+>", " ", content or "")
    plain = _re.sub(r"\s+", " ", plain).strip()[:6000]

    raw, model = llm.complete(prompts.ANALYST_EXTRACTION_SYSTEM,
                              prompts.build_analyst_extraction_user(plain),
                              max_tokens=1100, temperature=0.25, json_mode=True)
    data = llm.parse_json_loose(raw)
    notes = data.get("notes") or []
    updates = data.get("entity_updates") or {}
    src_norm = _norm_num(plain)
    print(f"\n  notes: {len(notes)} | entity_updates: {len(updates)} | model: {model}")
    flagged = 0
    for slug, summ in updates.items():
        nums = MONEY_RE.findall(str(summ)) + PCT_RE.findall(str(summ))
        bad = [n for n in nums if _norm_num(n) not in src_norm]
        status = mark(not bad)
        print(f"   {status}  {slug}: {summ}")
        for n in bad:
            print(f"        ❌ figure not in digest text: {n!r}")
            flagged += 1
    print(f"\n  {mark(flagged == 0)}  {flagged} ungrounded figure(s) across all entity_updates")


# ── Test 5: Narrative synthesis ──────────────────────────────────────────────
def test_narratives() -> None:
    hr("TEST 5 — NARRATIVE SYNTHESIS: thesis grounded in cluster signals")
    from app import db
    try:
        narr = db.get_narratives_with_signals() if hasattr(db, "get_narratives_with_signals") else []
    except Exception as e:
        print(f"  (could not load narratives: {e})")
        return
    if not narr:
        print("  (no narratives stored — run app.narratives first)")
        return
    for n in narr[:3]:
        sigs = n.get("signals") or []
        sig_text = " ".join((s.get("title") or "") for s in sigs).lower()
        thesis = (n.get("thesis") or "")
        # crude grounding: do the thesis's entity tokens appear in signal titles?
        ents = n.get("entity_slugs") or []
        hit = sum(1 for e in ents if e.split("-")[0] in sig_text)
        print(f"\n• {n.get('label')}  [{n.get('state')}]  signals={len(sigs)}")
        print(f"    entities {ents} — {hit}/{len(ents)} appear in signal titles")
        print(f"    thesis: {thesis[:220]}")


TESTS = {
    "digest": test_digest, "bullet": test_bullet, "agent": test_agent,
    "analyst": test_analyst, "narratives": test_narratives,
}

if __name__ == "__main__":
    print(f"LLM QUALITY AUDIT — {datetime.now(timezone.utc).isoformat()}")
    which = sys.argv[1:] or list(TESTS)
    for name in which:
        fn = TESTS.get(name)
        if not fn:
            print(f"unknown test: {name} (have: {', '.join(TESTS)})")
            continue
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"\n❌ test {name} crashed: {e}")
            traceback.print_exc()
    print("\ndone.")
