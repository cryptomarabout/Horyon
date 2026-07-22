"""Entity audit + maintenance tool.

Commands:
  python -m app.entity_audit stats               — coverage stats on recent digest bullets
  python -m app.entity_audit merge               — merge duplicate entity_memory entries
  python -m app.entity_audit dealias             — strip piggyback aliases (a big entity's ticker on small look-alikes)
  python -m app.entity_audit backfill-handles    — assign twitter_handle from co-occurring mentions
  python -m app.entity_audit reextract [--days N]— re-run LLM entity extraction on recent feed items
  python -m app.entity_audit seed-from-feeds     — seed entities from curated nitter feed sources
  python -m app.entity_audit backfill-avatars    — fill logo_url/twitter_handle for curated entities the auto-seeds miss
  python -m app.entity_audit all                 — run all of the above in order
"""
from __future__ import annotations

import argparse
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import db, entities, llm, prompts

log = logging.getLogger(__name__)

_NORM_RE = re.compile(r"[-\s_.]")


def _norm(s: str) -> str:
    """Normalise a name for duplicate detection: lowercase, strip separators."""
    return _NORM_RE.sub("", s.lower())


# Trailing org/generic tokens that denote the SAME entity:
# "Aerodrome Finance" == "Aerodrome", "NEAR Protocol" == "NEAR", "BNB Chain" == "BNB".
_SUFFIX_TOKENS = {"protocol", "network", "finance", "labs", "lab",
                  "money", "foundation", "chain"}
_MERGE_SPLIT = re.compile(r"[-\s_.]+")


def _merge_key(name: str) -> str:
    """Stronger dedup key: drop trailing generic suffix tokens before normalising,
    so version/suffix variants collapse together (but distinct cores stay apart)."""
    toks = [t for t in _MERGE_SPLIT.split((name or "").lower()) if t]
    while len(toks) > 1 and toks[-1] in _SUFFIX_TOKENS:
        toks.pop()
    return "".join(toks) or _norm(name)


# Curated cross-form equivalences the heuristic can't see (ticker / abbreviation
# == project, cross-TYPE same project, sub-brand == parent). Each set is folded into
# ONE node regardless of type — bypasses the same-type guard, so keep them verified +
# conservative (only fold things that are genuinely the SAME entity, never a
# parent/child of DIFFERENT scope like Coinbase ≠ Coinbase Ventures).
_EXPLICIT_MERGES = [
    {"bsc", "bnb-chain"},
    {"aero", "aerodrome", "aerodrome-finance"},
    {"hyperliquid", "hype"},
    {"ethereum", "ether"},          # 'Ether' (the asset) == Ethereum (junk extraction w/ wrong @ether_fi)
    {"kelp-dao", "kelp"},           # KelpDAO (dao) == Kelp (protocol) — same project, split by type
    {"zcash", "zec"},               # Zcash (protocol) == zec (chain facet) — same coin, split by type
    {"robinhood-chain", "robinhood-app-chain", "robinhood-crypto-chain"},  # same L2, fragmented
    # by article phrasing ("Robinhood App Chain" / "Robinhood Crypto Chain" vs "Robinhood Chain");
    # NOT the same as robinhood-canonical-bridge (a distinct bridge contract, different scope).
    # NB: Ondo's sub-brands (ondo-global-markets, ondo-yield-assets) are DISTINCT
    # DeFiLlama protocols with their own real TVL — do NOT merge them. The "3 Ondo
    # tags" problem is fixed by `dealias` instead (strip their piggyback bare 'ondo'
    # alias so only Ondo Finance matches a bare "Ondo" headline).
]


def _defillama_slugs() -> set[str]:
    """Slugs present in defillama_protocols — preferred as the surviving canonical
    so the entity keeps its TVL + logo join after a merge."""
    try:
        with db._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT slug FROM defillama_protocols")
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return set()


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #

def cmd_stats() -> None:
    print("\n=== Entity Detection Coverage (last 10 days of digest bullets) ===\n")

    with db._conn() as conn, conn.cursor() as cur:
        # Total entities
        cur.execute("SELECT count(*), sum(mention_count) FROM entity_memory")
        n_ent, total_mc = cur.fetchone()
        print(f"entity_memory: {n_ent} entities, {total_mc} total mentions")

        cur.execute("SELECT count(*) FROM defillama_protocols")
        n_def = cur.fetchone()[0]
        print(f"defillama_protocols: {n_def} protocols")

        cur.execute("""
            SELECT count(*) FROM entity_memory e
            JOIN defillama_protocols d ON d.slug = e.slug
        """)
        overlap = cur.fetchone()[0]
        print(f"entity_memory ∩ defillama_protocols (by slug): {overlap}")

        cur.execute("SELECT count(*) FROM entity_memory WHERE twitter_handle IS NOT NULL")
        with_handle = cur.fetchone()[0]
        print(f"entities with twitter_handle: {with_handle} / {n_ent}\n")

        _STOP = ("'chain','free','idle','defi','token','tokens',"
                 "'network','protocol','finance','open','world',"
                 "'new','core','main','node','fund','labs'")
        _DL = f"""
            EXISTS (
              SELECT 1 FROM defillama_protocols d
              WHERE NOT (lower(d.name) = ANY(ARRAY[{_STOP}]))
                AND (
                  title ~* ('\\y' || replace(replace(d.name,'.','[.]'),'+','[+]') || '\\y')
                  OR (d.name LIKE '% %' AND length(split_part(d.name,' ',1)) >= 4
                      AND NOT (lower(split_part(d.name,' ',1)) = ANY(ARRAY[{_STOP}]))
                      AND title ~* ('\\y' || replace(replace(split_part(d.name,' ',1),'.','[.]'),'+','[+]') || '\\y'))
                )
            )"""
        _EM = """
            EXISTS (
              SELECT 1 FROM entity_memory e
              WHERE e.mention_count >= 1 AND e.type NOT IN ('other')
                AND (
                  EXISTS (
                    SELECT 1 FROM unnest(e.aliases) alias
                    WHERE length(alias) >= 4 AND alias NOT LIKE '@%'
                """ + f"AND NOT (lower(alias) = ANY(ARRAY[{_STOP}]))" + """
                      AND title ~* ('\\y' || replace(replace(alias,'.','[.]'),'+','[+]') || '\\y')
                  )
                  OR (e.name LIKE '% %' AND length(split_part(e.name,' ',1)) >= 6
                      AND title ~* ('\\y' || replace(replace(split_part(e.name,' ',1),'.','[.]'),'+','[+]') || '\\y'))
                  OR (e.name NOT LIKE '% %' AND length(e.name) BETWEEN 3 AND 5
                      AND e.mention_count >= 10
                      AND e.type IN ('protocol','chain','dao','exchange','fund')
                      AND title ~* ('\\y' || replace(replace(e.name,'.','[.]'),'+','[+]') || '\\y'))
                )
            )"""
        cur.execute(f"""
            WITH bullets AS (
                SELECT unnest(string_to_array(content, E'•')) AS bullet
                FROM crypto_digest WHERE date >= current_date - 10
            ),
            titles AS (
                SELECT regexp_replace(trim(bullet), E'<[^>]*>', '', 'g') AS title
                FROM bullets
                WHERE length(trim(bullet)) > 20
                  AND trim(bullet) NOT LIKE '#%%'
                LIMIT 80
            )
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE {_DL}) AS defillama,
                count(*) FILTER (WHERE {_EM}) AS entity_mem,
                count(*) FILTER (WHERE NOT {_DL} AND NOT {_EM}) AS no_match
            FROM titles
        """)
        row = cur.fetchone()
        total, defillama, entity_mem, no_match = row
        covered = total - no_match
        print(f"Bullet coverage (last 10 days, n={total}):")
        print(f"  DeFiLlama match:     {defillama:3d} ({100*defillama//total if total else 0}%)")
        print(f"  entity_memory match: {entity_mem:3d} ({100*entity_mem//total if total else 0}%)")
        print(f"  Either:              {covered:3d} ({100*covered//total if total else 0}%)")
        print(f"  No match:            {no_match:3d} ({100*no_match//total if total else 0}%)")

        if no_match > 0:
            cur.execute(f"""
                WITH bullets AS (
                    SELECT unnest(string_to_array(content, E'•')) AS bullet
                    FROM crypto_digest WHERE date >= current_date - 10
                ),
                titles AS (
                    SELECT regexp_replace(trim(bullet), E'<[^>]*>', '', 'g') AS title
                    FROM bullets
                    WHERE length(trim(bullet)) > 20
                      AND trim(bullet) NOT LIKE '#%%'
                    LIMIT 80
                )
                SELECT left(title, 90) FROM titles
                WHERE NOT {_DL} AND NOT {_EM}
                LIMIT 20
            """)
            print("\n  Unmatched bullet samples:")
            for (t,) in cur.fetchall():
                if t and len(t.strip()) > 20:
                    print(f"    - {t.strip()[:88]}")


# --------------------------------------------------------------------------- #
# Merge duplicates
# --------------------------------------------------------------------------- #

def cmd_merge(dry_run: bool = False) -> None:
    print("\n=== Merging Duplicate Entity Memory Entries ===\n")

    with db._conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT slug, name, type, aliases, mention_count, twitter_handle, summary
            FROM entity_memory ORDER BY mention_count DESC
        """)
        rows = cur.fetchall()

    dl_slugs = _defillama_slugs()
    by_slug: dict[str, dict] = {}
    for slug, name, type_, aliases, mc, th, summary in rows:
        by_slug[slug] = {
            "slug": slug, "name": name, "type": type_,
            "aliases": list(aliases or []),
            "mention_count": mc, "twitter_handle": th, "summary": summary,
        }

    # Cluster key = (type, suffix-stripped name). Same-type guard prevents folding
    # an exchange into a fund (e.g. Coinbase ≠ Coinbase Ventures).
    cluster_of: dict[str, str] = {
        s: f"{m['type']}::{_merge_key(m['name'])}" for s, m in by_slug.items()
    }
    # Fold curated explicit equivalences into a single shared cluster id.
    for grp in _EXPLICIT_MERGES:
        present = [s for s in grp if s in cluster_of]
        if len(present) > 1:
            target = cluster_of[present[0]]
            for s in present:
                old = cluster_of[s]
                if old != target:
                    for s2, k in list(cluster_of.items()):
                        if k == old:
                            cluster_of[s2] = target

    groups: dict[str, list] = defaultdict(list)
    for slug, key in cluster_of.items():
        groups[key].append(by_slug[slug])

    # Only groups with duplicates
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"Found {len(dupes)} duplicate groups ({sum(len(v)-1 for v in dupes.values())} entries to remove)")

    merged = 0
    removed = 0
    for norm_name, members in dupes.items():
        # Canonical = prefer a slug that exists in defillama_protocols (keeps TVL +
        # logo join), then highest mention_count, then alphabetical for stability.
        members.sort(key=lambda m: (m["slug"] in dl_slugs, m["mention_count"], m["slug"]), reverse=True)
        canon = members[0]
        rest = members[1:]

        # Merge aliases, twitter_handle, mention_count into canonical
        all_aliases = set(canon["aliases"])
        for m in rest:
            all_aliases.update(m["aliases"])
        merged_mc = sum(m["mention_count"] for m in members)
        merged_th = canon["twitter_handle"] or next(
            (m["twitter_handle"] for m in rest if m["twitter_handle"]), None
        )
        merged_summary = canon["summary"] or next(
            (m["summary"] for m in rest if m["summary"]), None
        )

        if dry_run:
            print(f"  WOULD KEEP {canon['slug']} ({canon['mention_count']}mc)"
                  f" ← merge {[m['slug'] for m in rest]}")
            continue

        with db._conn() as conn, conn.cursor() as cur:
            # Update canonical
            cur.execute(
                """UPDATE entity_memory
                   SET aliases = %s, mention_count = %s,
                       twitter_handle = %s, summary = %s, updated_at = now()
                   WHERE slug = %s""",
                (list(all_aliases), merged_mc, merged_th, merged_summary, canon["slug"]),
            )
            # Delete dupes
            for m in rest:
                cur.execute("DELETE FROM entity_memory WHERE slug = %s", (m["slug"],))
                removed += 1
        merged += 1

    if not dry_run:
        print(f"Merged {merged} groups, removed {removed} duplicate entries.")


# --------------------------------------------------------------------------- #
# Strip piggyback aliases
# --------------------------------------------------------------------------- #

# Don't dealias unless the owner is clearly the one true holder of the token.
_DEALIAS_OWNER_MIN_MC = 10   # owner must be at least this mentioned
_DEALIAS_DOMINANCE = 3       # owner.mc must be ≥ this × the next-largest holder
_DEALIAS_TOKEN_MAXLEN = 8    # only short single-token aliases (tickers / bare names)
_DEALIAS_LOSER_MAX_MC = 10   # only strip from MINOR look-alikes — a well-mentioned entity
                             # (e.g. bnb-chain mc52) keeps its ticker even if a bigger
                             # neighbour (binance mc199) coincidentally carries the alias


def cmd_dealias(dry_run: bool = False) -> None:
    """Strip 'piggyback' aliases: a bare distinctive token (ticker / single-word name)
    that is really the identity of a MUCH larger, distinct entity but got attached as
    an alias to a smaller look-alike. The smaller entity then false-matches every
    headline mentioning the big one's ticker.

    Examples this kills:
      • 'hype' lives on Hyperliquid (mc 418) AND on Hyperion DeFi / Hyperliquid HLP /
        Hyperbeat (mc 1-2) → those tag every $HYPE story. Strip from the small ones.
      • 'ondo' lives on Ondo Finance (mc 56) AND on Ondo Global Markets / Yield Assets
        → tag every Ondo story. Strip from the small ones.

    The web first-word dedup can't fold these (names differ: Hyperion vs Hyperliquid),
    so the fix has to be at the alias level. Conservative: only strips a token when one
    owner dominates (≥3× the next holder, mc ≥ 10), only from holders < owner.mc/3, and
    never strips an entity's OWN name/slug (that's a same-name collision → merge/delete).
    """
    print("\n=== Stripping piggyback aliases ===\n")

    with db._conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT slug, name, aliases, mention_count FROM entity_memory"
        )
        rows = cur.fetchall()

    ents = [
        {"slug": s, "name": n, "aliases": list(a or []), "mc": mc or 0}
        for s, n, a, mc in rows
    ]

    # token (lowercased single-word alias) → list of holder entities
    holders: dict[str, list[dict]] = defaultdict(list)
    for e in ents:
        seen = set()
        for a in e["aliases"]:
            al = (a or "").strip().lower()
            # Only alphanumeric tickers / bare names can be headline-matched piggybacks.
            # Excludes @handles, $cashtags, hyphenated slug-forms (cme-group, usd-coin)
            # and dotted names — none of those appear verbatim in prose, so they never
            # cause the false-tag this pass exists to fix.
            if (not al or al in seen or not al.isalnum() or al.isdigit()
                    or len(al) < 3 or len(al) > _DEALIAS_TOKEN_MAXLEN
                    or al in entities.GENERIC_TERMS):
                continue
            seen.add(al)
            holders[al].append(e)

    strips: list[tuple[dict, str, dict]] = []  # (loser, token, owner)
    for token, hs in holders.items():
        if len(hs) < 2:
            continue
        hs_sorted = sorted(hs, key=lambda e: e["mc"], reverse=True)
        owner = hs_sorted[0]
        second_mc = hs_sorted[1]["mc"]
        if owner["mc"] < _DEALIAS_OWNER_MIN_MC or owner["mc"] < _DEALIAS_DOMINANCE * max(second_mc, 1):
            continue
        cutoff = owner["mc"] / _DEALIAS_DOMINANCE
        for loser in hs_sorted[1:]:
            # Only strip from clearly-minor look-alikes (both far smaller than the owner
            # AND below an absolute ceiling) so a substantial entity is never de-aliased.
            if loser["mc"] >= cutoff or loser["mc"] > _DEALIAS_LOSER_MAX_MC:
                continue
            # Never strip an entity's OWN identity (slug / normalised name) — that's a
            # same-name collision, not a piggyback (handle via merge/delete instead).
            if token == _norm(loser["name"]) or token == _norm(loser["slug"]):
                continue
            # Don't orphan: the loser must keep at least one matchable alias.
            remaining = [a for a in loser["aliases"] if a.strip().lower() != token]
            if not any(entities.matchable_term(a) or " " in a for a in remaining):
                continue
            strips.append((loser, token, owner))

    print(f"Found {len(strips)} piggyback aliases to strip")
    stripped = 0
    for loser, token, owner in strips:
        if dry_run:
            print(f"  WOULD STRIP {token!r} from {loser['slug']} ({loser['mc']}mc)"
                  f" — owned by {owner['slug']} ({owner['mc']}mc)")
            continue
        with db._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE entity_memory SET aliases = array_remove(aliases, %s),"
                " updated_at = now() WHERE slug = %s",
                (token, loser["slug"]),
            )
        stripped += 1

    if dry_run:
        print("(dry_run — no changes made)")
    else:
        print(f"Stripped {stripped} piggyback aliases.")


# --------------------------------------------------------------------------- #
# Purge zero-footprint collision-risk / generically-named junk entities
# --------------------------------------------------------------------------- #

def _entity_footprint(slug: str, name: str) -> list[str]:
    """Evidence the entity has REAL coverage under this slug — non-empty means
    'do not auto-delete, a human must look'. Mirrors the manual review procedure
    from /audit-data (graph edges, intel brief, avatar, digest coverage)."""
    fp: list[str] = []
    with db._conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM entity_edges WHERE slug_a = %s OR slug_b = %s",
                    (slug, slug))
        n = cur.fetchone()[0]
        if n:
            fp.append(f"{n} graph edges")
        cur.execute("SELECT 1 FROM entity_intel_brief WHERE lower(entity_name) = lower(%s) LIMIT 1",
                    (name,))
        if cur.fetchone():
            fp.append("intel brief")
        try:
            cur.execute("SELECT 1 FROM entity_avatars WHERE slug = %s LIMIT 1", (slug,))
            if cur.fetchone():
                fp.append("cached avatar")
        except Exception:
            conn.rollback()
        cur.execute("SELECT COALESCE(digest_mention_count, 0) FROM entity_memory WHERE slug = %s",
                    (slug,))
        row = cur.fetchone()
        if row and row[0]:
            fp.append(f"digest coverage ({row[0]})")
    return fp


def purge_collisions(dry_run: bool = False, quiet: bool = False) -> dict:
    """Auto-resolve the audit's review-only entity classes with graduated actions.

    Candidates = high-collision-risk entities (a bare name/alias is common prose —
    'firm'/'would'/'zero') and generically-NAMED junk ('Gas'/'Bridge'). Per entity:

    • colliding term is a NON-IDENTITY alias (piggyback like 'alpha' on AlphaFi Agg,
      'elon' on Echelon Market) → STRIP the alias (never the entity), unless that
      would orphan it. Deleting an entity over a mere alias is never right.
    • colliding term IS the entity's own name/slug identity → DELETE + durably
      BLOCK re-creation (entity_review verdict='block'), but ONLY when provably
      junk: zero footprint (no entity_edges, intel brief, cached avatar, or digest
      coverage), mc≤2, and NOT listed in defillama_protocols (a DeFiLlama slug is
      a real product by construction — decayed mention_count doesn't make it junk).
    • anything else → printed for human review; confirm real projects with
      `entity_audit keep <slug>` so they stop being flagged forever.

    Codifies the 2026-07-02 manual backlog review. A wrong block is reversible:
    `entity_audit keep <slug>` flips the verdict and extraction re-creates the
    entity on its next genuine mention.
    """
    from . import audit
    P = (lambda *a: None) if quiet else print
    P("\n=== Purging junk entities / stripping collision aliases ===\n")

    hits = audit.check_collision_risk()
    _junk, _generic, generic_name, _ct, _vc = audit.check_entity_hygiene()
    dl_slugs = _defillama_slugs()

    # slug → {term, ...} colliding terms (lowercased, deduped)
    flagged: dict[str, dict[str, str]] = defaultdict(dict)
    for slug, term, mc, feed_hits in hits:
        flagged[slug][term.lower()] = f"'{term}' = common prose ({feed_hits} feed hits vs mc={mc})"
    for slug, name, mc in generic_name:
        flagged[slug][_norm(name)] = f"generically-named junk ('{name}', mc={mc}, no distinctive alias)"

    deleted, stripped, review = 0, 0, []
    for slug, terms in sorted(flagged.items()):
        with db._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT name, COALESCE(mention_count,0), COALESCE(aliases,'{}') "
                "FROM entity_memory WHERE slug = %s", (slug,))
            row = cur.fetchone()
        if not row:
            continue
        name, mc, aliases = row
        why = "; ".join(terms.values())
        identity = {_norm(name), _norm(slug), (name or "").lower().strip()}
        identity_hit = any(t in identity for t in terms)

        if identity_hit:
            fp = _entity_footprint(slug, name)
            if fp or mc > 2 or slug in dl_slugs:
                if slug in dl_slugs:
                    fp = fp + ["DeFiLlama-listed"]
                review.append((slug, name, mc, fp, why))
                continue
            if dry_run:
                P(f"  WOULD DELETE+BLOCK {slug!r} ({name}) — {why}")
            else:
                with db._conn() as conn, conn.cursor() as cur:
                    cur.execute("DELETE FROM entity_memory WHERE slug = %s", (slug,))
                db.set_entity_review(slug, "block", why[:500])
                P(f"  DELETED+BLOCKED {slug!r} ({name}) — {why}")
            deleted += 1
            continue

        # Non-identity piggyback alias(es): strip them, keep the entity.
        to_strip = [a for a in aliases if (a or "").strip().lower() in terms]
        if not to_strip:
            review.append((slug, name, mc, [], why))
            continue
        remaining = [a for a in aliases if a not in to_strip]
        keeps_identity = (" " in (name or "")) or any(
            " " in a or entities.matchable_term(a) for a in remaining)
        if not keeps_identity:
            review.append((slug, name, mc, ["strip would orphan"], why))
            continue
        if dry_run:
            P(f"  WOULD STRIP {to_strip} from {slug!r} ({name}) — {why}")
        else:
            with db._conn() as conn, conn.cursor() as cur:
                for a in to_strip:
                    cur.execute(
                        "UPDATE entity_memory SET aliases = array_remove(aliases, %s), "
                        "updated_at = now() WHERE slug = %s", (a, slug))
            P(f"  STRIPPED {to_strip} from {slug!r} ({name}) — {why}")
        stripped += len(to_strip)

    if review:
        P(f"\n  {len(review)} candidates need human review:")
        for slug, name, mc, fp, why in review[:25]:
            P(f"    ? {slug!r} ({name}, mc={mc}) footprint: {', '.join(fp) or 'mc>2'}")
            P(f"        flagged: {why}")
            P(f"        real project → python3 -m app.entity_audit keep {slug}")
        if len(review) > 25:
            P(f"    … +{len(review) - 25} more")

    P(f"\n{'Would delete' if dry_run else 'Deleted'} {deleted} junk entities, "
      f"{'would strip' if dry_run else 'stripped'} {stripped} piggyback aliases "
      f"({len(review)} left for review).")
    return {"deleted": deleted, "aliases_stripped": stripped,
            "review": len(review), "dry_run": dry_run}


def cmd_keep(slugs: list[str]) -> None:
    """Record a human 'keep' verdict: the entity is real; stop flagging it."""
    for slug in slugs:
        with db._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM entity_memory WHERE slug = %s", (slug,))
            row = cur.fetchone()
        if not row:
            print(f"  SKIP {slug!r} — not in entity_memory")
            continue
        db.set_entity_review(slug, "keep", "human-confirmed real project")
        print(f"  KEEP {slug!r} ({row[0]}) — recorded; audit will stop flagging it")


# --------------------------------------------------------------------------- #
# Backfill twitter_handle from co-occurring feed mentions
# --------------------------------------------------------------------------- #

def cmd_backfill_handles(dry_run: bool = False) -> None:
    """For each entity without a twitter_handle, scan feed_items.mentions
    for handles that frequently co-occur with the entity name in the content.
    Assign the handle if it's a close slug/name match.
    """
    print("\n=== Backfilling twitter_handle from feed mentions ===\n")

    with db._conn() as conn, conn.cursor() as cur:
        # Get entities without a handle
        cur.execute("""
            SELECT slug, name, aliases
            FROM entity_memory
            WHERE twitter_handle IS NULL AND mention_count >= 2
            ORDER BY mention_count DESC
        """)
        entities_rows = cur.fetchall()
        print(f"Entities without twitter_handle (mention_count >= 2): {len(entities_rows)}")

        # For each entity, find top co-occurring mentions
        assigned = 0
        for slug, name, aliases in entities_rows:
            name_lower = name.lower()
            # Score each mention by frequency in items containing this entity name.
            # Word-boundary match (\y), not a raw substring, to avoid piggyback false
            # positives (e.g. 'arb' matching inside 'arbitrum') when assigning handles.
            cur.execute("""
                SELECT mention, count(*) AS freq
                FROM feed_items fi, unnest(fi.mentions) AS mention
                WHERE fi.content ~* %s
                  AND mention NOT IN ('@bitcoin','@ethereum','@defi','@crypto','@web3','@nft')
                GROUP BY mention
                ORDER BY freq DESC
                LIMIT 5
            """, (r"\y" + re.escape(name) + r"\y",))
            candidates = cur.fetchall()

            best_handle = None
            best_score = 0
            slug_norm = slug.replace("-", "").lower()
            name_norm = _norm(name)
            for handle, freq in candidates:
                h_bare = handle.lstrip("@").lower()
                score = 0
                # Exact match: handle == slug (no hyphens) or slug literal
                if h_bare == slug_norm or h_bare == slug:
                    score = 100
                # Handle has a short suffix/prefix vs slug (e.g. @polymarketbets vs polymarket)
                elif h_bare.startswith(slug_norm) and len(h_bare) - len(slug_norm) <= 3:
                    score = 70
                elif slug_norm.startswith(h_bare) and len(slug_norm) - len(h_bare) <= 3:
                    score = 70
                # Handle matches normalised name exactly
                elif h_bare == name_norm:
                    score = 90
                # Handle starts with or equals normalised name with short suffix
                elif h_bare.startswith(name_norm) and len(h_bare) - len(name_norm) <= 3:
                    score = 60
                # No partial/substring matches — they produce too many false positives

                # Boost by frequency
                if score > 0:
                    score += min(freq, 20)

                if score > best_score:
                    best_score = score
                    best_handle = handle

            # Require score >= 70 to avoid substring false positives (sec→@securitize etc.)
            if best_handle and best_score >= 70:
                if dry_run:
                    print(f"  WOULD SET {slug!r} → {best_handle} (score={best_score})")
                else:
                    cur.execute(
                        "UPDATE entity_memory SET twitter_handle = %s WHERE slug = %s",
                        (best_handle, slug),
                    )
                    assigned += 1

    if not dry_run:
        print(f"Assigned twitter_handle to {assigned} entities.")
    else:
        print("(dry_run — no changes made)")


# --------------------------------------------------------------------------- #
# Re-extract entities from recent feed items
# --------------------------------------------------------------------------- #

def cmd_reextract(days: int = 7) -> None:
    """Re-run LLM entity extraction on recent feed items (beyond just new ones).
    Useful after improving the extraction prompt or lowering max_tokens.
    """
    print(f"\n=== Re-extracting entities from last {days} days of feed items ===\n")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db._conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT link, content, mentions::text[], pub_date::text
               FROM feed_items
               WHERE ingested_at >= %s
               ORDER BY pub_date DESC
               LIMIT 500""",
            (cutoff,),
        )
        rows = cur.fetchall()

    items = [
        {"link": r[0], "content": r[1], "mentions": r[2] or [], "pub_date": r[3] or ""}
        for r in rows
    ]
    print(f"Processing {len(items)} items from last {days} days")

    if not items:
        print("No items found.")
        return

    # Split into batches to avoid token limits
    batch_size = 60
    total_upserted = 0
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start: batch_start + batch_size]
        n = entities.extract_and_upsert_entities(batch)
        total_upserted += n
        print(f"  Batch {batch_start//batch_size + 1}: upserted {n} entities")

    print(f"\nTotal entities upserted: {total_upserted}")


# --------------------------------------------------------------------------- #
# Seed entities from curated nitter feed sources
# --------------------------------------------------------------------------- #

# Curated mapping: (twitter_handle, slug, display_name, type)
# Only includes accounts that represent DeFi-relevant entities (protocols, chains, funds,
# exchanges) — excludes analytics tools, news outlets, and individual researchers.
_FEED_ENTITY_SEEDS = [
    # Chains / L2s
    ("ethereum",       "ethereum",             "Ethereum",             "chain"),
    ("base",           "base",                 "Base",                 "chain"),
    ("Optimism",       "optimism",             "Optimism",             "chain"),
    ("Arbitrum",       "arbitrum",             "Arbitrum",             "chain"),
    ("LineaBuild",     "linea",                "Linea",                "chain"),
    ("inkonchain",     "ink",                  "Ink",                  "chain"),
    # Protocols / Infrastructure
    ("LayerZero_Core", "layerzero",            "LayerZero",            "protocol"),
    ("aave",           "aave",                 "Aave",                 "protocol"),
    ("Uniswap",        "uniswap",              "Uniswap",              "protocol"),
    ("Morpho",         "morpho",               "Morpho",               "protocol"),
    ("ethena",         "ethena",               "Ethena",               "protocol"),
    ("gauntlet_xyz",   "gauntlet",             "Gauntlet",             "protocol"),
    ("arc",            "arc",                  "Arc",                  "chain"),
    # DAOs
    ("SnapshotLabs",   "snapshot-labs",        "Snapshot Labs",        "dao"),
    # Funds / VCs
    ("a16zcrypto",     "a16z-crypto",          "a16z Crypto",          "fund"),
    ("paraficapital",  "paradigm",             "Paradigm",             "fund"),
    ("ForesightVen",   "foresight-ventures",   "Foresight Ventures",   "fund"),
    ("jump_",          "jump-crypto",          "Jump Crypto",          "fund"),
    ("wintermute_t",   "wintermute",           "Wintermute",           "fund"),
    # Exchanges
    ("coinbase",       "coinbase",             "Coinbase",             "exchange"),
    ("binance",        "binance",              "Binance",              "exchange"),
    ("krakenfx",       "kraken",               "Kraken",               "exchange"),
    # Ethereum Foundation
    ("ethereumfndn",   "ethereum-foundation",  "Ethereum Foundation",  "fund"),
]


def cmd_seed_from_feeds(dry_run: bool = False) -> None:
    """Upsert entity_memory entries from the curated nitter feed source list.

    For each seed: creates the entity if absent, or updates twitter_handle if missing.
    Aliases are generated from slug and lowercased name. Does NOT overwrite existing
    summaries or aliases already accumulated from LLM extraction.
    """
    print("\n=== Seeding entities from feed sources ===\n")

    from datetime import date as date_cls
    today = date_cls.today()
    created = 0
    updated = 0

    for handle, slug, name, type_ in _FEED_ENTITY_SEEDS:
        twitter_handle = f"@{handle}"

        with db._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT slug, twitter_handle FROM entity_memory WHERE slug = %s",
                (slug,),
            )
            existing = cur.fetchone()

        if existing is None:
            # New entity — create with seed aliases
            aliases = list({slug, name.lower()})
            if dry_run:
                print(f"  WOULD CREATE {slug!r} ({name}, {type_}, {twitter_handle})")
            else:
                db.upsert_entity(slug, name, type_, aliases,
                                 last_mentioned=today, twitter_handle=twitter_handle)
                created += 1
        else:
            ex_slug, ex_handle = existing
            if ex_handle is None:
                # Entity exists but lacks a twitter handle
                if dry_run:
                    print(f"  WOULD UPDATE {slug!r} twitter_handle → {twitter_handle}")
                else:
                    with db._conn() as conn, conn.cursor() as cur:
                        cur.execute(
                            "UPDATE entity_memory SET twitter_handle = %s WHERE slug = %s",
                            (twitter_handle, slug),
                        )
                    updated += 1
            # else: already has a handle, skip

    if not dry_run:
        print(f"Created {created} new entities, updated {updated} twitter handles.")
    else:
        print("(dry_run — no changes made)")


# --------------------------------------------------------------------------- #
# Curated avatar backfill for high-visibility entities the auto-seeds miss
# --------------------------------------------------------------------------- #

# The entity map shows a logo when an entity has a logo_url (DeFiLlama/CoinGecko)
# OR a twitter_handle (resolved to its X avatar via unavatar.io); otherwise it
# falls back to a bare monogram, which is the eyesore visible on the map. The
# CoinGecko auto-seed only fills logo_url when slug == gecko-id, so tokens whose
# slug differs from their gecko-id (aster vs aster-2, usde vs ethena-usde, …)
# never get a logo. Curate the gaps here: token logos from CoinGecko (the exact
# token image — more accurate than the issuer's X avatar) and org/exchange X
# handles for non-token entities. Ambiguous or no-good-source entities
# (ef-protocol, spcx, orchard, x402, individuals) are intentionally left as
# monograms — a wrong logo is worse than none. Slug → stable CoinGecko logo URL.
_ENTITY_LOGO_SEEDS = {
    "aster":  "https://coin-images.coingecko.com/coins/images/69040/large/_ASTER.png",
    "usde":   "https://coin-images.coingecko.com/coins/images/33613/large/usde.png",
    "pyusd":  "https://coin-images.coingecko.com/coins/images/31212/large/PYUSD_Token_Logo_2x.png",
    "rlusd":  "https://coin-images.coingecko.com/coins/images/39651/large/RLUSD_200x200_%281%29.png",
    "purr":   "https://coin-images.coingecko.com/coins/images/37125/large/PURR_CG.png",
    "mythos": "https://coin-images.coingecko.com/coins/images/28045/large/Mythos_Logos_200x200.png",
    "venice": "https://coin-images.coingecko.com/coins/images/54023/large/VVV_Token_Transparent.png",
}
# Slug → X handle (resolved to an avatar by unavatar.io at render time).
_ENTITY_HANDLE_SEEDS = {
    "debank":  "@DeBankDeFi",
    "nobitex": "@nobitex",
    "ice":     "@ICE_Markets",
    "ibit":    "@iShares",
}


def cmd_backfill_avatars(dry_run: bool = False) -> None:
    """Fill logo_url / twitter_handle for curated high-visibility entities that the
    automatic CoinGecko/DeFiLlama seeds miss, so they show an avatar on the map.

    Only fills when the field is currently NULL (never overwrites an existing logo
    or handle) and only touches entities that already exist in entity_memory.
    """
    print("\n=== Backfilling avatars for curated entities ===\n")
    logos = 0
    handles = 0
    with db._conn() as conn, conn.cursor() as cur:
        for slug, logo_url in _ENTITY_LOGO_SEEDS.items():
            cur.execute(
                "SELECT logo_url FROM entity_memory WHERE slug = %s", (slug,))
            row = cur.fetchone()
            if row is None:
                print(f"  SKIP {slug!r} — not in entity_memory")
                continue
            if row[0]:
                continue  # already has a logo
            if dry_run:
                print(f"  WOULD SET {slug!r} logo_url → {logo_url}")
            else:
                cur.execute(
                    "UPDATE entity_memory SET logo_url = %s, updated_at = now() "
                    "WHERE slug = %s AND logo_url IS NULL",
                    (logo_url, slug))
                logos += 1
        for slug, handle in _ENTITY_HANDLE_SEEDS.items():
            cur.execute(
                "SELECT twitter_handle FROM entity_memory WHERE slug = %s", (slug,))
            row = cur.fetchone()
            if row is None:
                print(f"  SKIP {slug!r} — not in entity_memory")
                continue
            if row[0]:
                continue  # already has a handle
            if dry_run:
                print(f"  WOULD SET {slug!r} twitter_handle → {handle}")
            else:
                cur.execute(
                    "UPDATE entity_memory SET twitter_handle = %s, updated_at = now() "
                    "WHERE slug = %s AND twitter_handle IS NULL",
                    (handle, slug))
                handles += 1

    if dry_run:
        print("(dry_run — no changes made)")
    else:
        print(f"Set {logos} logos and {handles} twitter handles.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    logging.basicConfig(level=logging.WARNING)

    ap = argparse.ArgumentParser(description="Entity memory audit and maintenance")
    ap.add_argument("command", choices=[
        "stats", "merge", "dealias", "purge-collisions", "keep", "backfill-handles",
        "reextract", "seed-from-feeds", "seed-from-coingecko", "backfill-avatars", "all",
    ])
    ap.add_argument("slugs", nargs="*", help="Entity slugs (for the `keep` command)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would be done without writing")
    ap.add_argument("--days", type=int, default=7, help="Days of history for reextract (default: 7)")
    args = ap.parse_args()

    cmd = args.command
    if cmd == "keep":
        if not args.slugs:
            ap.error("keep requires at least one slug")
        cmd_keep(args.slugs)
        print("\nDone.")
        return
    if cmd in ("stats", "all"):
        cmd_stats()
    if cmd in ("merge", "all"):
        cmd_merge(dry_run=args.dry_run)
    if cmd in ("dealias", "all"):
        cmd_dealias(dry_run=args.dry_run)
    if cmd in ("purge-collisions", "all"):
        purge_collisions(dry_run=args.dry_run)
    if cmd in ("backfill-handles", "all"):
        cmd_backfill_handles(dry_run=args.dry_run)
    if cmd in ("seed-from-feeds", "all"):
        cmd_seed_from_feeds(dry_run=args.dry_run)
    if cmd in ("backfill-avatars", "all"):
        cmd_backfill_avatars(dry_run=args.dry_run)
    if cmd in ("seed-from-coingecko",):
        from .defillama import fetch_and_seed_coingecko
        n = fetch_and_seed_coingecko()
        print(f"Seeded {n} CoinGecko coins into entity_memory.")
    if cmd in ("reextract", "all"):
        cmd_reextract(days=args.days)

    print("\nDone.")


if __name__ == "__main__":
    main()
