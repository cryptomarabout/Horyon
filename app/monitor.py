"""Monitoring dashboard (Flask). Shows container status, ingestion stats, digest
history, keyword analysis history, and per-source health (Twitter vs News).
Served behind Caddy at /monitor. All times displayed in UTC+2.

In dev mode (BOT_USE_POLLING=true) an extra "Database" panel appears at the top
with controls to restore a prod backup or wipe to an empty state.  The backup
dir (~/.horyon-db-backups on the host, bind-mounted at /horyon-backups in the
container via docker-compose.dev.yml) is scanned for available dumps.

The panel is hidden — and the restore/wipe endpoint returns 403 — whenever
DATABASE_URL points at a REMOTE host (external-db / test-machine mode against the
prod DB). That way the destructive controls can never be aimed at prod; the
read-only DB role on the test machine is the second, DB-enforced layer.
"""
from __future__ import annotations

import glob
import hmac
import io
import logging
import os
import re
import tarfile
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template_string, request

from . import config, db, embeddings, util
from .feeds import SOURCES

log = logging.getLogger(__name__)
app = Flask(__name__)


@app.before_request
def _require_basic_auth():
    """Gate the whole dashboard behind HTTP Basic Auth when credentials are
    configured. No-op when unset (prod sits behind Caddy basic-auth instead).
    Guards the DB restore/wipe panel on publicly-exposed dev/test boxes."""
    # /healthz must stay open — the container healthcheck hits it without
    # credentials; gating it would peg the monitor 'unhealthy' forever.
    if request.path == "/healthz":
        return None
    user = config.MONITOR_AUTH_USER
    pw = config.MONITOR_AUTH_PASS
    if not (user and pw):
        return None
    auth = request.authorization
    ok = (
        auth is not None
        and hmac.compare_digest(auth.username or "", user)
        and hmac.compare_digest(auth.password or "", pw)
    )
    if not ok:
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Horyon Monitor"'},
        )
    return None

UTC2 = timezone(timedelta(hours=2))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
# Hosts that mean "our own local db container", i.e. NOT the prod DB reached over
# an SSH tunnel. Anything else (host.docker.internal, a real IP/hostname) is remote.
_LOCAL_DB_HOSTS = {"db", "localhost", "127.0.0.1", "::1", "", None}


def _db_is_remote() -> bool:
    """True when DATABASE_URL points at a non-local host (external-db/test mode).

    Used to keep the destructive 'Database' panel + its endpoint OFF whenever the
    monitor is wired to the prod DB through the SSH tunnel, so a wipe/restore can
    never be aimed at prod. Belt to the read-only DB role's suspenders.
    """
    try:
        host = urlsplit(config.DATABASE_URL).hostname
    except Exception:
        return False  # un-parseable → treat as local (dev default), don't over-block
    return host not in _LOCAL_DB_HOSTS


def _fmt(dt, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a datetime in UTC+2."""
    if not dt:
        return "—"
    if isinstance(dt, datetime):
        return util.as_utc(dt).astimezone(UTC2).strftime(fmt)
    return str(dt)[:16]


def _short_model(model: str) -> str:
    """'provider/model-name:tag' → 'model-name'."""
    if not model:
        return "—"
    return model.split("/")[-1].split(":")[0]


def _source_key(url: str) -> tuple[str, str]:
    """(source_type, key) matching get_source_ingestion_counts() result keys."""
    if "nitter.net" in url:
        parts = url.split("nitter.net/")
        handle = parts[1].split("/")[0].lower() if len(parts) > 1 else url
        return "twitter", handle
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.removeprefix("www.")
    return "news", domain


def _source_display(url: str) -> str:
    """Human-readable source name from URL."""
    if "nitter.net" in url:
        parts = url.split("/")
        return "@" + parts[3] if len(parts) > 3 else url
    # News: domain + last meaningful path segment (skip rss/feed/feeds/tag)
    from urllib.parse import urlparse
    p = urlparse(url)
    domain = p.netloc.removeprefix("www.")
    skip = {"rss", "feed", "feeds", "tag", ""}
    segs = [s for s in p.path.split("/") if s not in skip]
    if segs:
        return f"{domain} · {segs[-1]}"
    return domain


def _fmt_tvl(usd: float) -> str:
    """Format a TVL dollar value as $42.1B, $1.5T, etc."""
    if usd >= 1e12:
        return f"${usd / 1e12:.2f}T"
    if usd >= 1e9:
        return f"${usd / 1e9:.1f}B"
    if usd >= 1e6:
        return f"${usd / 1e6:.0f}M"
    return f"${usd:,.0f}"


def _fmt_dur(ms: int | None) -> str:
    """Format a millisecond duration for display."""
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    m, s = divmod(ms // 1000, 60)
    return f"{m}m {s}s"


# --------------------------------------------------------------------------- #
# Dev-mode data-switch state + helpers
# --------------------------------------------------------------------------- #

_restore_state: dict = {"status": "idle", "message": ""}
_restore_lock = threading.Lock()


def _list_backups() -> list[dict]:
    """Return available backup dates from BACKUP_DIR, newest first."""
    pattern = os.path.join(config.BACKUP_DIR, "crypto-*.dump.part*")
    sizes: dict[str, int] = {}
    for p in glob.glob(pattern):
        m = re.search(r"crypto-(\d{4}-\d{2}-\d{2})\.dump\.part", p)
        if m:
            d = m.group(1)
            sizes[d] = sizes.get(d, 0) + os.path.getsize(p)
    return [
        {"date": d, "size": f"{sizes[d] // 1024 // 1024}MB"}
        for d in sorted(sizes, reverse=True)
    ]


def _restore_worker(date_str: str, password: str) -> None:
    global _restore_state
    try:
        with _restore_lock:
            _restore_state = {"status": "running", "message": f"reading parts for {date_str}…"}

        pattern = os.path.join(config.BACKUP_DIR, f"crypto-{date_str}.dump.part*")
        parts = sorted(glob.glob(pattern))
        if not parts:
            raise FileNotFoundError(f"No backup parts for {date_str} in {config.BACKUP_DIR}")

        dump_bytes = bytearray()
        for p in parts:
            with open(p, "rb") as f:
                dump_bytes.extend(f.read())

        total_mb = len(dump_bytes) // 1024 // 1024
        with _restore_lock:
            _restore_state["message"] = f"uploading {total_mb}MB to horyon-db…"

        # Wrap in a tar archive so we can use put_archive (avoids stdin piping)
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            info = tarfile.TarInfo(name="restore.dump")
            info.size = len(dump_bytes)
            tar.addfile(info, io.BytesIO(bytes(dump_bytes)))
        tar_buf.seek(0)

        import docker as _docker
        client = _docker.from_env()
        db_container = client.containers.get("horyon-db")
        db_container.put_archive("/tmp", tar_buf)
        del dump_bytes, tar_buf  # free memory before the long restore

        with _restore_lock:
            _restore_state["message"] = "running pg_restore (this takes a minute)…"

        result = db_container.exec_run(
            ["pg_restore", "-U", "crypto", "-d", "crypto",
             "--clean", "--if-exists", "--no-owner", "--no-privileges",
             "/tmp/restore.dump"],
            environment={"PGPASSWORD": password},
        )
        db_container.exec_run(["rm", "-f", "/tmp/restore.dump"])

        if result.exit_code != 0:
            stderr = (result.output or b"").decode(errors="replace")[-400:]
            raise RuntimeError(f"pg_restore exited {result.exit_code}: {stderr}")

        with _restore_lock:
            _restore_state = {"status": "done", "message": f"restored {date_str} ({total_mb}MB) ✓"}

    except Exception as exc:
        log.error("db restore failed: %s", exc)
        with _restore_lock:
            _restore_state = {"status": "error", "message": str(exc)[:300]}


def _wipe_worker() -> None:
    global _restore_state
    try:
        with _restore_lock:
            _restore_state = {"status": "running", "message": "truncating all tables…"}

        with db._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
            tables = [r[0] for r in cur.fetchall()]

        if tables:
            tlist = ", ".join(f'"{t}"' for t in tables)
            with db._conn() as conn, conn.cursor() as cur:
                cur.execute(f"TRUNCATE {tlist} CASCADE")

        with _restore_lock:
            _restore_state = {"status": "done", "message": f"wiped {len(tables)} tables ✓"}

    except Exception as exc:
        log.error("db wipe failed: %s", exc)
        with _restore_lock:
            _restore_state = {"status": "error", "message": str(exc)[:300]}


def _containers() -> list[dict]:
    try:
        import docker
        client = docker.from_env()
        out = []
        for c in client.containers.list(all=True):
            if not c.name.startswith(config.CONTAINER_PREFIX):
                continue
            state = c.attrs.get("State", {})
            health = (state.get("Health") or {}).get("Status")
            image = c.attrs.get("Config", {}).get("Image", "?")
            started_raw = state.get("StartedAt", "")
            started = "—"
            if started_raw and started_raw != "0001-01-01T00:00:00Z":
                try:
                    dt = datetime.fromisoformat(started_raw[:19]).replace(tzinfo=timezone.utc)
                    started = _fmt(dt)
                except Exception:
                    started = started_raw[:16]
            out.append({
                "name": c.name,
                "status": c.status,
                "health": health or "—",
                "started": started,
                "image": image,
            })
        return sorted(out, key=lambda x: x["name"])
    except Exception as exc:
        log.warning("docker status unavailable: %s", exc)
        return [{"name": "docker socket unavailable", "status": str(exc)[:80],
                 "health": "—", "started": "—", "image": "—"}]


def _q1(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


# Kaiko Research lands in feed_items as creator='Kaiko' (links under kaiko.com).
# The ILIKE pattern is a bound param (_KAIKO_LINK) — embedding a literal '%…%' in
# SQL passed to execute() with a params tuple makes psycopg2 read the % as a
# placeholder and raise "tuple index out of range".
_KAIKO_MATCH = "creator = 'Kaiko' OR link ILIKE %s"
_KAIKO_LINK = "%kaiko.com%"


def _kaiko() -> dict:
    """Kaiko Research ingestion health + where Kaiko articles are actually used.

    Two questions the dashboard answers: (1) is the 6h sitemap scrape pulling
    articles into feed_items, and (2) which daily-digest bullets cite a Kaiko URL
    (i.e. where the content surfaces downstream). Graceful — never raises."""
    out: dict = {"total": 0, "citations": [], "articles": []}
    try:
        with db._conn() as conn, conn.cursor() as cur:
            total, k7, k30, last_ing, last_pub = _q1(cur, f"""
                SELECT count(*),
                       count(*) FILTER (WHERE COALESCE(pub_date, ingested_at) >= now()-interval '7 days'),
                       count(*) FILTER (WHERE COALESCE(pub_date, ingested_at) >= now()-interval '30 days'),
                       max(ingested_at), max(COALESCE(pub_date, ingested_at))
                  FROM feed_items WHERE {_KAIKO_MATCH}""", (_KAIKO_LINK,))

            cur.execute(f"""
                SELECT link, content, COALESCE(pub_date, ingested_at) AS ts
                  FROM feed_items WHERE {_KAIKO_MATCH}
                 ORDER BY ts DESC LIMIT 12""", (_KAIKO_LINK,))
            recent = cur.fetchall()

            # narrative signals whose source URL is a Kaiko article (another use site)
            signals = 0
            try:
                signals = _q1(cur,
                    "SELECT count(*) FROM narrative_signals WHERE url ILIKE %s",
                    ("%kaiko.com%",))[0]
            except Exception:
                pass

            # every digest whose stored content references a Kaiko link
            cur.execute(
                "SELECT date, content FROM crypto_digest WHERE content ILIKE %s ORDER BY date DESC",
                ("%kaiko.com%",))
            dig_rows = cur.fetchall()
    except Exception as exc:
        log.debug("kaiko stats failed: %s", exc)
        return out

    # Parse digest bullets and keep only those whose CITED link points at kaiko.com
    # (mirrors _parse_digest_bullets: one bullet per line starting with •).
    citations: list[dict] = []
    for d, content in dig_rows:
        for line in (content or "").replace("\r", "").split("\n"):
            t = line.strip()
            if not t.startswith("•"):
                continue
            link_m = re.search(r'<a[^>]*href="([^"]+)"', t, re.I)
            if not (link_m and "kaiko.com" in link_m.group(1).lower()):
                continue
            title_m = re.search(r"<b>([\s\S]*?)</b>", t, re.I)
            title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else "—"
            citations.append({"date": str(d), "title": title, "link": link_m.group(1)})

    def _title(content: str) -> str:
        # Kaiko items are stored as "Title. Summary" — recover the headline.
        head = re.split(r"\.\s", content or "", 1)[0].strip()
        return head[:100] if head else "—"

    out.update({
        "total": total or 0,
        "last_7d": k7 or 0,
        "last_30d": k30 or 0,
        "last_ingested": _fmt(last_ing) if last_ing else "—",
        "last_published": _fmt(last_pub) if last_pub else "—",
        "signals": signals,
        "citations": citations,
        "cited_count": len(citations),
        "cited_digests": len({c["date"] for c in citations}),
        "articles": [{"link": r[0], "title": _title(r[1]), "ts": _fmt(r[2])} for r in recent],
    })
    return out


def _pipeline_health() -> dict:
    """Surface silent degradation (T9): the states that historically rotted for
    days without anyone noticing — failed source fetches, failed/missing audio
    variants, thread backlog, frozen governance states, unembedded items, and
    digest_audio storage growth (retention watch). Graceful — never raises."""
    out: dict = {}
    try:
        with db._conn() as conn, conn.cursor() as cur:
            # (1) Items still unembedded after 2+ ingest cycles. Fresh rows are
            # legitimately NULL until the next embed pass — only overdue ones are red.
            out["emb_overdue"] = _q1(cur, """
                SELECT count(*) FROM feed_items
                 WHERE embedding IS NULL
                   AND ingested_at < now() - interval '45 minutes'""")[0]

            # (2) Sources failing hard. Historically 2,513 failed fetches sat log-only;
            # >10 consecutive failures usually means the URL moved, not a blip.
            cur.execute("""
                SELECT url, consecutive_failures, last_status, left(last_error,100)
                  FROM source_health
                 WHERE consecutive_failures > 10
                 ORDER BY consecutive_failures DESC""")
            out["failing_sources"] = [
                {"display": _source_display(r[0]), "url": r[0], "failures": r[1],
                 "status": r[2], "error": (r[3] or "")}
                for r in cur.fetchall()
            ]

            # (3a) Audio rows not 'ready' in the last 14 days. failed=red;
            # blocked is the fail-closed modality gate doing its job (amber);
            # pending is only suspicious for a past date.
            cur.execute("""
                SELECT digest_date, variant, status
                  FROM digest_audio
                 WHERE digest_date >= current_date - 14 AND status <> 'ready'
                 ORDER BY digest_date DESC, variant""")
            out["audio_bad"] = [
                {"date": str(r[0]), "variant": r[1], "status": r[2]}
                for r in cur.fetchall()
            ]
            # Days (excluding today — it may still be rendering) with <3 ready variants.
            cur.execute("""
                SELECT d.date::text, COALESCE(sum((da.status='ready')::int),0) AS ready
                  FROM (SELECT generate_series(current_date-7, current_date-1,
                                               interval '1 day')::date AS date) d
                  LEFT JOIN digest_audio da ON da.digest_date = d.date
                 GROUP BY d.date
                HAVING COALESCE(sum((da.status='ready')::int),0) < 3
                 ORDER BY d.date DESC""")
            out["audio_incomplete_days"] = [
                {"date": r[0], "ready": r[1]} for r in cur.fetchall()
            ]

            # (3b) Thread backlog. 'pending' is NOT a failure while posting is
            # unoperated (decision D1) — neutral count; 'blocked' is the modality gate.
            cur.execute("SELECT status, count(*) FROM digest_threads GROUP BY status")
            tc = dict(cur.fetchall())
            out["threads"] = {"pending": tc.get("pending", 0),
                              "posted": tc.get("posted", 0),
                              "blocked": tc.get("blocked", 0)}

            # (4) Governance sanity: an 'active' proposal past its end_ts means the
            # fetcher is frozen (state never advanced to closed).
            out["gov_frozen"] = _q1(cur, """
                SELECT count(*) FROM governance_proposals
                 WHERE state = 'active' AND end_ts < now()""")[0]

            # (6) scored LLM eval runs (weekly cron + manual — app/eval_harness.py)
            try:
                from .db import evals as _evals
                out["eval_batches"] = [
                    {**b, "run_at": _fmt(b["run_at"])} for b in _evals.get_eval_batches(8)
                ]
            except Exception:
                out["eval_batches"] = []

            # (5) digest_audio stored bytes — watch the retention cron working.
            total_bytes, oldest = _q1(cur, """
                SELECT COALESCE(sum(byte_size),0), min(digest_date)
                  FROM digest_audio WHERE audio IS NOT NULL""")
            out["audio_mb"] = round((total_bytes or 0) / 1e6)
            out["audio_oldest"] = str(oldest) if oldest else "—"
            retention = config.AUDIO_RETENTION_DAYS or 0
            out["audio_retention_ok"] = True
            if retention and oldest:
                age = (datetime.now(timezone.utc).date() - oldest).days
                out["audio_retention_ok"] = age <= retention + 2
    except Exception as exc:
        log.debug("pipeline health stats failed: %s", exc)
    return out


def gather() -> dict:
    with db._conn() as conn, conn.cursor() as cur:
        feed_total, feed_emb, feed_null, feed_stale, last_ing = _q1(cur, """
            SELECT count(*), count(embedding), count(*) FILTER (WHERE embedding IS NULL),
                   count(*) FILTER (WHERE embed_version < %s), max(ingested_at)
            FROM feed_items""", (embeddings.EMBED_VERSION,))
        last_1h, last_24h = _q1(cur, """
            SELECT count(*) FILTER (WHERE ingested_at >= now()-interval '1 hour'),
                   count(*) FILTER (WHERE ingested_at >= now()-interval '24 hours')
            FROM feed_items""")
        cur.execute("SELECT source_type, count(*) FROM feed_items GROUP BY source_type ORDER BY 2 DESC")
        by_type = cur.fetchall()

        cur.execute("""SELECT started_at, raw, cleaned, inserted, embedded,
                              sources_ok, sources_failed, duration_ms
                       FROM ingest_run ORDER BY id DESC LIMIT 10""")
        raw_runs = cur.fetchall()

        src_total, src_ok, src_failed = _q1(cur, """
            SELECT count(*), count(*) FILTER (WHERE last_ok), count(*) FILTER (WHERE NOT last_ok)
            FROM source_health""")
        cur.execute("""SELECT url, last_ok, last_status, last_item_count, consecutive_failures,
                              left(last_error,120), updated_at
                       FROM source_health ORDER BY last_ok ASC, consecutive_failures DESC, url""")
        all_sources_raw = cur.fetchall()

        dig_count, dig_last = _q1(cur, "SELECT count(*), max(date) FROM crypto_digest")
        chat_msgs, chat_users = _q1(cur, "SELECT count(*), count(DISTINCT chat_id) FROM chat_history")

    # Format ingest runs
    runs = [
        {
            "started": _fmt(r[0]),
            "raw": r[1], "cleaned": r[2], "inserted": r[3], "embedded": r[4],
            "sources_ok": r[5], "sources_failed": r[6], "duration": _fmt_dur(r[7]),
        }
        for r in raw_runs
    ]

    # Split sources: nitter.net = twitter, else = news
    def _fmt_src(row):
        return {
            "url": row[0],
            "display": _source_display(row[0]),
            "ok": row[1],
            "status": row[2],
            "count": row[3],
            "failures": row[4],
            "error": (row[5] or "")[:80],
            "updated": _fmt(row[6], "%H:%M"),
        }

    twitter_sources = [_fmt_src(s) for s in all_sources_raw if "nitter.net" in s[0]]
    news_sources    = [_fmt_src(s) for s in all_sources_raw if "nitter.net" not in s[0]]
    twitter_ok   = sum(1 for s in twitter_sources if s["ok"])
    news_ok      = sum(1 for s in news_sources if s["ok"])
    twitter_fail = len(twitter_sources) - twitter_ok
    news_fail    = len(news_sources) - news_ok

    # Recent digests (graceful if migration not yet applied)
    recent_digests: list[dict] = []
    try:
        rows = db.get_recent_digests(15)
        recent_digests = [
            {
                "time": _fmt(r[0]),
                "date": str(r[1]),
                "model": _short_model(r[2]),
                "trigger": r[3] or "manual",
                "duration": _fmt_dur(r[4]),
                "error": (r[5] or "")[:120],
            }
            for r in rows
        ]
    except Exception as exc:
        log.debug("get_recent_digests failed (migration pending?): %s", exc)

    # Recent keyword analyses (graceful if migration not yet applied)
    recent_analyses: list[dict] = []
    try:
        rows = db.get_recent_keyword_analyses(15)
        recent_analyses = [
            {
                "time": _fmt(r[0]),
                "keyword": (r[1] or "")[:80],
                "model": _short_model(r[2]),
                "duration": _fmt_dur(r[3]),
            }
            for r in rows
        ]
    except Exception as exc:
        log.debug("get_recent_keyword_analyses failed (migration pending?): %s", exc)

    # TVL snapshot (graceful if migration not yet applied)
    tvl_rows: list[dict] = []
    try:
        for date, chain, tvl in db.get_latest_tvl():
            tvl_rows.append({"date": str(date), "chain": chain, "tvl": _fmt_tvl(tvl)})
    except Exception as exc:
        log.debug("get_latest_tvl failed (migration pending?): %s", exc)

    # Enrich source rows with per-source ingestion counts (24h / 7d / total)
    raw_counts = db.get_source_ingestion_counts()
    count_map: dict[tuple, tuple] = {(r[0], r[1]): r[2:] for r in raw_counts}
    for s in twitter_sources + news_sources:
        _, key = _source_key(s["url"])
        stype = "twitter" if "nitter.net" in s["url"] else "news"
        counts = count_map.get((stype, key), (0, 0, 0, None))
        s["ing_total"], s["ing_7d"], s["ing_24h"] = counts[0], counts[1], counts[2]
    twitter_sources.sort(key=lambda x: x["ing_24h"] or 0, reverse=True)
    news_sources.sort(key=lambda x: x["ing_24h"] or 0, reverse=True)

    protocols_total = 0
    try:
        with db._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM defillama_protocols")
            protocols_total = cur.fetchone()[0]
    except Exception as exc:
        log.debug("get protocols count failed: %s", exc)

    kaiko = _kaiko()

    # Dev-mode data-switch panel
    with _restore_lock:
        restore_snap = dict(_restore_state)
    db_switch: dict = {}
    if config.BOT_USE_POLLING and not _db_is_remote():
        db_switch = {
            "backups": _list_backups(),
            "status": restore_snap["status"],
            "message": restore_snap["message"],
            "backup_dir": config.BACKUP_DIR,
        }

    return {
        "now": datetime.now(timezone.utc).astimezone(UTC2).strftime("%Y-%m-%d %H:%M:%S UTC+2"),
        "is_dev": config.BOT_USE_POLLING,
        "containers": _containers(),
        "feed": {
            "total": feed_total, "embedded": feed_emb, "null": feed_null,
            "stale": feed_stale,
            "last_ingested": _fmt(last_ing) if last_ing else "—",
            "last_1h": last_1h, "last_24h": last_24h, "by_type": by_type,
        },
        "runs": runs,
        "src": {
            "total": src_total, "ok": src_ok, "failed": src_failed,
            "twitter": {"rows": twitter_sources, "ok": twitter_ok,
                        "total": len(twitter_sources), "failed": twitter_fail},
            "news":    {"rows": news_sources,    "ok": news_ok,
                        "total": len(news_sources),    "failed": news_fail},
        },
        "content": {
            "digests": dig_count,
            "last_digest": str(dig_last) if dig_last else "—",
            "chat_msgs": chat_msgs,
            "chat_users": chat_users,
            "protocols": protocols_total,
        },
        "recent_digests": recent_digests,
        "recent_analyses": recent_analyses,
        "tvl": tvl_rows,
        "kaiko": kaiko,
        "pipeline": _pipeline_health(),
        "db_switch": db_switch,
    }


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
CONTENT = """
{% if db_switch %}
<h2 style="margin-top:16px">Database <span style="font-weight:400;text-transform:none;letter-spacing:0;font-size:11px;color:#8b949e">— dev mode</span></h2>
<div class="db-panel">
  <div class="db-row">
    <span class="db-badge">DEV</span>
    <span class="db-stat">{{ feed.total }} feed items &nbsp;·&nbsp; last digest {{ content.last_digest }}</span>
    {% if db_switch.status == 'running' %}
    <span class="db-msg muted">⏳ {{ db_switch.message }}</span>
    {% elif db_switch.status == 'done' %}
    <span class="db-msg ok">{{ db_switch.message }}</span>
    {% elif db_switch.status == 'error' %}
    <span class="db-msg bad">{{ db_switch.message }}</span>
    {% endif %}
  </div>
  <div class="db-row db-actions">
    {% if db_switch.backups %}
    <span class="db-label">Prod backup:</span>
    <select id="db-date" class="db-select">
      {% for b in db_switch.backups %}
      <option value="{{ b.date }}">{{ b.date }} ({{ b.size }})</option>
      {% endfor %}
    </select>
    <button class="btn-restore" onclick="dbRestore()" {{ 'disabled' if db_switch.status == 'running' else '' }}>
      Load prod data
    </button>
    {% else %}
    <span class="muted small">No backups in <code>{{ db_switch.backup_dir }}</code> — run <code>scripts/db-restore.sh --pull</code> on the host first.</span>
    {% endif %}
    <button class="btn-wipe" onclick="dbWipe()" {{ 'disabled' if db_switch.status == 'running' else '' }}
      style="margin-left:auto">Wipe to empty</button>
  </div>
</div>
{% endif %}

<p class="ts">{{ now }} &nbsp;·&nbsp; <span class="live-dot"></span> live refresh every 15s</p>

<h2>Containers</h2>
<table>
<tr><th>Name</th><th>Status</th><th>Health</th><th>Started (UTC+2)</th><th>Image</th></tr>
{% for c in containers %}<tr>
 <td class="mono">{{ c.name }}</td>
 <td><span class="pill {{ 'pill-run' if c.status=='running' else ('pill-exit' if c.status=='exited' else 'pill-down') }}">{{ c.status }}</span></td>
 <td class="{{ 'ok' if c.health=='healthy' else ('bad' if c.health=='unhealthy' else 'muted') }}">{{ c.health }}</td>
 <td class="muted">{{ c.started }}</td>
 <td class="muted mono small">{{ c.image }}</td>
</tr>{% endfor %}
</table>

<h2>Ingestion</h2>
<div class="grid">
 <div class="card"><div class="n">{{ feed.total }}</div><div class="l">feed items</div></div>
 <div class="card"><div class="n {{ 'bad' if feed.null else 'ok' }}">{{ feed.embedded }}</div><div class="l">embedded{% if feed.null %} <span class="bad">({{ feed.null }} null)</span>{% endif %}</div></div>
 <div class="card"><div class="n {{ 'bad' if feed.stale else 'ok' }}">{{ feed.stale }}</div><div class="l">stale embeds</div></div>
 <div class="card"><div class="n">{{ feed.last_1h }}</div><div class="l">last 1h</div></div>
 <div class="card"><div class="n">{{ feed.last_24h }}</div><div class="l">last 24h</div></div>
 <div class="card"><div class="n small">{{ feed.last_ingested }}</div><div class="l">last ingest (UTC+2)</div></div>
 {% for t,n in feed.by_type %}<div class="card"><div class="n">{{ n }}</div><div class="l">{{ t }}</div></div>{% endfor %}
</div>

<h2>Pipeline health</h2>
{% if pipeline %}
<div class="grid">
 <div class="card"><div class="n {{ 'bad' if pipeline.emb_overdue else 'ok' }}">{{ pipeline.emb_overdue }}</div><div class="l">unembedded &gt;45min</div></div>
 <div class="card"><div class="n {{ 'bad' if pipeline.failing_sources else 'ok' }}">{{ pipeline.failing_sources|length }}</div><div class="l">sources failing &gt;10×</div></div>
 <div class="card"><div class="n {{ 'bad' if pipeline.audio_bad or pipeline.audio_incomplete_days else 'ok' }}">{{ pipeline.audio_bad|length }}</div><div class="l">audio not-ready 14d{% if pipeline.audio_incomplete_days %} <span class="bad">({{ pipeline.audio_incomplete_days|length }} incomplete day{{ 's' if pipeline.audio_incomplete_days|length != 1 }})</span>{% endif %}</div></div>
 <div class="card"><div class="n">{{ pipeline.threads.pending }}<span class="muted" style="font-size:13px"> pending</span>{% if pipeline.threads.blocked %} <span class="bad" style="font-size:15px">{{ pipeline.threads.blocked }} blocked</span>{% endif %}</div><div class="l">threads ({{ pipeline.threads.posted }} posted — pending ≠ failure, posting unoperated)</div></div>
 <div class="card"><div class="n {{ 'bad' if pipeline.gov_frozen else 'ok' }}">{{ pipeline.gov_frozen }}</div><div class="l">governance frozen-active</div></div>
 <div class="card"><div class="n {{ '' if pipeline.audio_retention_ok else 'bad' }}">{{ pipeline.audio_mb }} MB</div><div class="l">audio bytes (oldest {{ pipeline.audio_oldest }}{% if not pipeline.audio_retention_ok %} — <span class="bad">retention stalled</span>{% endif %})</div></div>
</div>

{% if pipeline.failing_sources %}
<h3 class="sub bad">Failing sources — check for a URL move first (feeds.py), drop only if the source rotted</h3>
<table>
<tr><th>Source</th><th>consecutive fails</th><th>HTTP</th><th>last error</th></tr>
{% for s in pipeline.failing_sources %}<tr class="row-err">
 <td class="mono small">{{ s.display }}</td>
 <td class="bad">{{ s.failures }}</td>
 <td>{{ s.status if s.status is not none else '—' }}</td>
 <td class="muted small">{{ s.error }}</td>
</tr>{% endfor %}
</table>
{% endif %}

{% if pipeline.audio_bad %}
<h3 class="sub">Audio variants not ready (14d) <span class="muted small">— failed heals same-day off the 20-min cycle; blocked = modality gate (deliberate)</span></h3>
<table>
<tr><th>Date</th><th>Variant</th><th>Status</th></tr>
{% for a in pipeline.audio_bad %}<tr class="{{ 'row-err' if a.status == 'failed' else '' }}">
 <td class="muted mono small">{{ a.date }}</td>
 <td>{{ a.variant }}</td>
 <td class="{{ 'bad' if a.status == 'failed' else 'muted' }}">{{ a.status }}</td>
</tr>{% endfor %}
</table>
{% endif %}

<h3 class="sub">LLM grounding eval <span class="muted small">— scored fixture runs (weekly cron Sun 20:10 UTC + manual; app/eval_harness.py)</span></h3>
{% if pipeline.eval_batches %}
<table>
<tr><th>Run (UTC+2)</th><th>Kind</th><th>Cases</th><th>Checks ok/fail</th><th>Failing case(s)</th></tr>
{% for e in pipeline.eval_batches %}<tr class="{{ 'row-err' if e.failed_cases else '' }}">
 <td class="muted mono small">{{ e.run_at }}</td>
 <td class="small">{{ e.run_kind }}</td>
 <td>{{ e.cases }}</td>
 <td><span class="ok">{{ e.passed }}</span> / <span class="{{ 'bad' if e.failed else 'muted' }}">{{ e.failed }}</span></td>
 <td class="{{ 'bad small' if e.failing else 'muted small' }}">{{ e.failing|join(', ') if e.failing else '—' }}</td>
</tr>{% endfor %}
</table>
{% else %}
<p class="muted">No eval runs recorded yet — <code>docker exec horyon-bot python3 -m app.eval_harness</code>.</p>
{% endif %}
{% else %}
<p class="muted">Pipeline health unavailable (query failed — check logs).</p>
{% endif %}

<h2>Recent ingest runs</h2>
<table>
<tr><th>Started (UTC+2)</th><th>raw</th><th>kept</th><th>new</th><th>embedded</th><th>sources ok / fail</th><th>Duration</th></tr>
{% for r in runs %}<tr>
 <td class="muted">{{ r.started }}</td>
 <td>{{ r.raw }}</td><td>{{ r.cleaned }}</td><td>{{ r.inserted }}</td><td>{{ r.embedded }}</td>
 <td><span class="ok">{{ r.sources_ok }}</span> / <span class="{{ 'bad' if r.sources_failed else 'muted' }}">{{ r.sources_failed }}</span></td>
 <td class="muted dur">{{ r.duration }}</td>
</tr>{% endfor %}
</table>

<h2>Kaiko Research <span style="font-weight:400;text-transform:none;letter-spacing:0;color:#8b949e;font-size:10px">(sitemap scrape · creator=Kaiko · 6h cron)</span></h2>
{% if kaiko.total %}
<div class="grid">
 <div class="card"><div class="n">{{ kaiko.total }}</div><div class="l">articles ingested</div></div>
 <div class="card"><div class="n">{{ kaiko.last_7d }}</div><div class="l">last 7d</div></div>
 <div class="card"><div class="n">{{ kaiko.last_30d }}</div><div class="l">last 30d</div></div>
 <div class="card"><div class="n small">{{ kaiko.last_ingested }}</div><div class="l">last ingest (UTC+2)</div></div>
 <div class="card"><div class="n {{ 'ok' if kaiko.cited_count else 'muted' }}">{{ kaiko.cited_count }}</div><div class="l">bullets citing Kaiko</div></div>
 <div class="card"><div class="n muted">{{ kaiko.signals }}</div><div class="l">narrative signals</div></div>
</div>

{% if kaiko.citations %}
<h3 class="sub">Bullets citing Kaiko <span class="muted small">— {{ kaiko.cited_count }} across {{ kaiko.cited_digests }} digest(s)</span></h3>
<table>
<tr><th>Digest date</th><th>Bullet</th><th>Source</th></tr>
{% for c in kaiko.citations %}<tr>
 <td class="muted mono small"><a href="https://app.horyon.xyz/d/{{ c.date }}" target="_blank" rel="noopener">{{ c.date }}</a></td>
 <td>{{ c.title }}</td>
 <td class="small"><a href="{{ c.link }}" target="_blank" rel="noopener">🔗 kaiko</a></td>
</tr>{% endfor %}
</table>
{% else %}
<p class="muted">No daily-digest bullets have cited a Kaiko article yet.</p>
{% endif %}

{% if kaiko.articles %}
<h3 class="sub">Recently ingested Kaiko articles</h3>
<table>
<tr><th>Date (UTC+2)</th><th>Title</th><th>Link</th></tr>
{% for a in kaiko.articles %}<tr>
 <td class="muted mono small">{{ a.ts }}</td>
 <td>{{ a.title }}</td>
 <td class="small"><a href="{{ a.link }}" target="_blank" rel="noopener">🔗</a></td>
</tr>{% endfor %}
</table>
{% endif %}
{% else %}
<p class="muted">No Kaiko articles ingested yet — runs every 6h (<code>app.kaiko</code>). Seed with <code>docker exec horyon-bot python3 -m app.kaiko --backfill</code>.</p>
{% endif %}

<h2>Daily digest history</h2>
{% if recent_digests %}
<table>
<tr><th>Time (UTC+2)</th><th>Date</th><th>Model</th><th>Trigger</th><th>Duration</th><th>Error</th></tr>
{% for d in recent_digests %}<tr class="{{ 'row-err' if d.error else '' }}">
 <td class="muted">{{ d.time }}</td>
 <td>{{ d.date }}</td>
 <td>{% if d.error %}<span class="badge-err">failed</span>{% else %}<span class="badge-model">{{ d.model }}</span>{% endif %}</td>
 <td><span class="pill {{ 'pill-cron' if d.trigger == 'cron' else 'pill-manual' }}">{{ '🤖 cron' if d.trigger == 'cron' else '👤 manual' }}</span></td>
 <td class="muted dur">{{ d.duration }}</td>
 <td class="muted small">{{ d.error }}</td>
</tr>{% endfor %}
</table>
{% else %}<p class="muted">No digest executions recorded yet.</p>{% endif %}

<h2>Keyword analysis history</h2>
{% if recent_analyses %}
<table>
<tr><th>Time (UTC+2)</th><th>Query</th><th>Model</th><th>Duration</th></tr>
{% for a in recent_analyses %}<tr>
 <td class="muted">{{ a.time }}</td>
 <td class="mono small">{{ a.keyword }}</td>
 <td><span class="badge-model">{{ a.model }}</span></td>
 <td class="muted dur">{{ a.duration }}</td>
</tr>{% endfor %}
</table>
{% else %}<p class="muted">No keyword analyses recorded yet.</p>{% endif %}

<h2>Source health</h2>
<div class="src-grid">

<details data-id="twitter" {{ 'open' if src.twitter.failed else '' }}>
 <summary class="src-header">
  <span class="src-toggle"></span>
  <span class="src-label">Twitter / Nitter</span>
  <span class="src-stat">
   <span class="ok">{{ src.twitter.ok }}</span><span class="muted">/{{ src.twitter.total }}</span>
   {% if src.twitter.failed %}<span class="badge-err">{{ src.twitter.failed }} failing</span>{% endif %}
  </span>
 </summary>
 <table class="src-table">
 <tr><th>Account</th><th>OK</th><th>HTTP</th><th>24h</th><th>7d</th><th>Total</th><th>fails</th><th>last error</th><th>updated</th></tr>
 {% for s in src.twitter.rows %}<tr class="{{ 'row-err' if not s.ok else '' }}">
  <td class="mono small">{{ s.display }}</td>
  <td class="{{ 'ok' if s.ok else 'bad' }}">{{ '✓' if s.ok else '✗' }}</td>
  <td>{{ s.status if s.status is not none else '—' }}</td>
  <td class="{{ 'ok' if s.ing_24h else 'muted' }}"><b>{{ s.ing_24h }}</b></td>
  <td>{{ s.ing_7d }}</td>
  <td class="muted">{{ s.ing_total }}</td>
  <td class="{{ 'bad' if s.failures else 'muted' }}">{{ s.failures }}</td>
  <td class="muted small">{{ s.error }}</td>
  <td class="muted">{{ s.updated }}</td>
 </tr>{% endfor %}
 </table>
</details>

<details data-id="news" {{ 'open' if src.news.failed else '' }}>
 <summary class="src-header">
  <span class="src-toggle"></span>
  <span class="src-label">News</span>
  <span class="src-stat">
   <span class="ok">{{ src.news.ok }}</span><span class="muted">/{{ src.news.total }}</span>
   {% if src.news.failed %}<span class="badge-err">{{ src.news.failed }} failing</span>{% endif %}
  </span>
 </summary>
 <table class="src-table">
 <tr><th>Source</th><th>OK</th><th>HTTP</th><th>24h</th><th>7d</th><th>Total</th><th>fails</th><th>last error</th><th>updated</th></tr>
 {% for s in src.news.rows %}<tr class="{{ 'row-err' if not s.ok else '' }}">
  <td class="mono small">{{ s.display }}</td>
  <td class="{{ 'ok' if s.ok else 'bad' }}">{{ '✓' if s.ok else '✗' }}</td>
  <td>{{ s.status if s.status is not none else '—' }}</td>
  <td class="{{ 'ok' if s.ing_24h else 'muted' }}"><b>{{ s.ing_24h }}</b></td>
  <td>{{ s.ing_7d }}</td>
  <td class="muted">{{ s.ing_total }}</td>
  <td class="{{ 'bad' if s.failures else 'muted' }}">{{ s.failures }}</td>
  <td class="muted small">{{ s.error }}</td>
  <td class="muted">{{ s.updated }}</td>
 </tr>{% endfor %}
 </table>
</details>

</div>

<h2>DeFi TVL <span style="font-weight:400;text-transform:none;letter-spacing:0;color:#58a6ff;font-size:10px">(DeFiLlama)</span></h2>
{% if tvl %}
<div class="grid">
{% for row in tvl %}
 <div class="card">
  <div class="n tvl-n">{{ row.tvl }}</div>
  <div class="l">{{ row.chain }}{% if row.chain == 'total' %} (all chains){% endif %}</div>
  <div class="tvl-date">{{ row.date }}</div>
 </div>
{% endfor %}
</div>
{% else %}<p class="muted">No TVL data yet — runs daily at 07:10 UTC.</p>{% endif %}


<h2>Content</h2>
<div class="grid">
 <div class="card"><div class="n">{{ content.digests }}</div><div class="l">digests (last {{ content.last_digest }})</div></div>
 <div class="card"><div class="n">{{ content.chat_msgs }}</div><div class="l">chat messages</div></div>
 <div class="card"><div class="n">{{ content.chat_users }}</div><div class="l">chats</div></div>
 <div class="card"><div class="n muted tvl-n">{{ content.protocols }}</div><div class="l">protocols in DB</div></div>
</div>
"""

SHELL = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Horyon · monitor</title>
<style>
/* ── Reset / base ──────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font:14px/1.6 -apple-system,"Segoe UI",Roboto,monospace;padding:0}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}

/* ── Layout ─────────────────────────────────────────────── */
.topbar{background:#161b22;border-bottom:1px solid #30363d;padding:14px 28px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:10}
.topbar h1{font-size:16px;font-weight:700;color:#e6edf3;letter-spacing:-.3px}
.topbar .ts{font-size:12px;color:#8b949e;margin-left:auto}
main{padding:24px 28px;max-width:1400px}

/* ── Typography helpers ─────────────────────────────────── */
h2{font-size:11px;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.8px;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #21262d}
h2:first-of-type{margin-top:16px}
h3.sub{font-size:11px;font-weight:600;color:#8b949e;margin:16px 0 8px;font-variant:normal}
.ts{font-size:12px;color:#8b949e;margin-bottom:20px}
.muted{color:#8b949e}
.ok{color:#3fb950}
.bad{color:#f85149;font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.small{font-size:12px}
p.muted{margin:8px 0 4px;font-size:13px}

/* ── Live dot ───────────────────────────────────────────── */
.live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#3fb950;
  animation:pulse 2s infinite;vertical-align:middle;margin-right:3px}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ── Cards ──────────────────────────────────────────────── */
.grid{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:4px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;min-width:130px;transition:border-color .15s}
.card:hover{border-color:#58a6ff44}
.card .n{font-size:24px;font-weight:700;line-height:1.1;color:#e6edf3}
.card .n.small{font-size:16px;padding-top:4px}
.card .l{color:#8b949e;font-size:11px;margin-top:3px;text-transform:uppercase;letter-spacing:.5px}

/* ── Tables ─────────────────────────────────────────────── */
table{border-collapse:collapse;width:100%;background:#161b22;border:1px solid #30363d;
  border-radius:10px;overflow:hidden;margin-bottom:4px}
th,td{text-align:left;padding:8px 13px;border-bottom:1px solid #21262d;font-size:13px}
th{color:#8b949e;font-weight:600;background:#0d1117;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
tr:last-child td{border-bottom:none}
tr:hover td{background:#1c2128}
.row-err td{background:#1a0d0d}
.row-err:hover td{background:#200e0e}

/* ── Pills / badges ─────────────────────────────────────── */
.pill{display:inline-block;padding:2px 9px;border-radius:12px;font-size:11px;font-weight:600;white-space:nowrap}
.pill-run   {background:#196c2e;color:#aff5b4}
.pill-exit  {background:#3d3d3d;color:#8b949e}
.pill-down  {background:#6d1f21;color:#ffa198}
.pill-cron  {background:#0c2d4c;color:#79c0ff;border:1px solid #1f6feb44}
.pill-manual{background:#2d2d2d;color:#8b949e;border:1px solid #30363d}
.badge-model{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;
  background:#1c2d3d;color:#79c0ff;border:1px solid #1f6feb44;font-family:ui-monospace,monospace}
.badge-err{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;
  font-weight:600;background:#6d1f21;color:#ffa198}

/* ── Source health ──────────────────────────────────────── */
.dur{font-variant-numeric:tabular-nums;white-space:nowrap}
.src-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:4px;align-items:start}
@media(max-width:900px){.src-grid{grid-template-columns:1fr}}
details{background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden}
details+details{margin-top:0}
summary.src-header{
  display:flex;align-items:center;gap:10px;
  padding:12px 16px;cursor:pointer;user-select:none;list-style:none;
  background:#161b22;
  font-size:13px;font-weight:600;
  transition:background .1s;
}
summary.src-header:hover{background:#1c2128}
details[open] summary.src-header{border-bottom:1px solid #21262d}
summary.src-header::-webkit-details-marker{display:none}
.src-toggle{width:18px;height:18px;display:flex;align-items:center;justify-content:center;
  background:#21262d;border-radius:4px;flex-shrink:0;transition:background .1s}
summary.src-header:hover .src-toggle{background:#30363d}
.src-toggle::before{content:"▶";font-size:9px;color:#8b949e;transition:transform .15s;display:block}
details[open] .src-toggle::before{transform:rotate(90deg)}
.src-label{color:#e6edf3}
.src-stat{margin-left:auto;font-size:12px;display:flex;align-items:center;gap:6px}
.src-table{border:none;border-radius:0;margin-bottom:0;background:transparent}
.src-table th{background:#0d1117;font-size:10px}
.src-table td{font-size:12px}
.tvl-n{color:#79c0ff!important}
.tvl-date{font-size:10px;color:#8b949e;margin-top:2px;font-family:ui-monospace,monospace}
.chg-up{color:#3fb950}.chg-dn{color:#f85149}.chg-neu{color:#8b949e}

/* ── Dev-mode DB switch panel ───────────────────────────────── */
.db-panel{background:#0d1117;border:1px solid #d4af3755;border-radius:10px;padding:14px 18px;margin-bottom:4px;display:flex;flex-direction:column;gap:10px}
.db-row{display:flex;align-items:center;flex-wrap:wrap;gap:10px}
.db-badge{background:#3a2800;color:#d4af37;border:1px solid #d4af3766;border-radius:6px;padding:2px 10px;font-size:11px;font-weight:700;letter-spacing:.5px;flex-shrink:0}
.db-stat{font-size:13px;color:#8b949e}
.db-msg{font-size:12px}
.db-label{font-size:12px;color:#8b949e;flex-shrink:0}
.db-actions{flex-wrap:wrap}
.db-select{background:#161b22;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;padding:4px 8px;font-size:13px;cursor:pointer}
.btn-restore,.btn-wipe{border:none;border-radius:6px;padding:5px 14px;font-size:13px;cursor:pointer;font-weight:500;transition:opacity .15s;white-space:nowrap}
.btn-restore{background:#196c2e;color:#aff5b4}
.btn-restore:hover:not(:disabled){opacity:.85}
.btn-wipe{background:#6d1f21;color:#ffa198}
.btn-wipe:hover:not(:disabled){opacity:.85}
.btn-restore:disabled,.btn-wipe:disabled{opacity:.35;cursor:not-allowed}
</style>
</head>
<body>
<div class="topbar">
  <h1>🦅 Horyon · monitor</h1>
  <div class="ts" id="topbar-ts">—</div>
</div>
<main>
<div id="dash">{{ inner|safe }}</div>
</main>
<script>
function fragURL(){
  const u=new URL(window.location.href);
  u.search='';
  u.pathname=u.pathname.replace(/\\/$/,'')+'/fragment';
  return u;
}
function saveOpenStates(){
  const s={};
  document.querySelectorAll('details[data-id]').forEach(el=>{ s[el.dataset.id]=el.open; });
  return s;
}
function restoreOpenStates(s){
  document.querySelectorAll('details[data-id]').forEach(el=>{
    if(el.dataset.id in s) el.open=s[el.dataset.id];
  });
}
async function tick(){
  try{
    const r=await fetch(fragURL(),{cache:'no-store'});
    if(r.ok){
      const states=saveOpenStates();
      document.getElementById('dash').innerHTML=await r.text();
      restoreOpenStates(states);
    }
  }catch(e){}
  document.getElementById('topbar-ts').textContent=new Date().toLocaleTimeString();
}
setInterval(tick,15000);
document.getElementById('topbar-ts').textContent=new Date().toLocaleTimeString();

async function dbRestore(){
  const sel=document.getElementById('db-date');
  if(!sel)return;
  const date=sel.value;
  if(!confirm('Load prod backup '+date+'?\nThis replaces ALL current data in horyon-db.'))return;
  const r=await fetch('/api/db-switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'restore',date})});
  if(!r.ok){const e=await r.json().catch(()=>({}));alert('Error: '+(e.error||r.status));}
  else setTimeout(tick,600);
}
async function dbWipe(){
  if(!confirm('Wipe all data?\nSchema is preserved; every row is deleted.'))return;
  const r=await fetch('/api/db-switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'wipe'})});
  if(!r.ok){const e=await r.json().catch(()=>({}));alert('Error: '+(e.error||r.status));}
  else setTimeout(tick,600);
}
</script>
</body></html>
"""


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    inner = render_template_string(CONTENT, **gather())
    return render_template_string(SHELL, inner=inner)


@app.route("/fragment")
def fragment():
    return render_template_string(CONTENT, **gather())


@app.route("/api/status")
def api_status():
    data = gather()
    # Make JSON-serialisable
    data["runs"] = [
        {k: str(v) if not isinstance(v, (int, float, bool, type(None))) else v
         for k, v in r.items()}
        for r in data["runs"]
    ]
    src = data["src"]
    for section in ("twitter", "news"):
        src[section]["rows"] = [
            {k: str(v) if not isinstance(v, (int, float, bool, type(None))) else v
             for k, v in s.items()}
            for s in src[section]["rows"]
        ]
    data["feed"]["by_type"] = [list(map(str, r)) for r in data["feed"]["by_type"]]
    return jsonify(data)


@app.route("/api/db-switch", methods=["GET", "POST"])
def api_db_switch():
    """Dev-only endpoint: list / trigger restore or wipe. 403 in prod."""
    if not config.BOT_USE_POLLING:
        return jsonify({"error": "only available in dev mode (BOT_USE_POLLING=true)"}), 403
    if _db_is_remote():
        return jsonify({"error": "disabled — monitor is pointed at a remote DB (external-db/test mode); refusing to wipe/restore prod"}), 403

    if request.method == "GET":
        with _restore_lock:
            return jsonify(dict(_restore_state))

    body = request.get_json(silent=True) or {}
    action = body.get("action", "")

    with _restore_lock:
        if _restore_state["status"] == "running":
            return jsonify({"error": "a job is already in progress"}), 409

    if action == "restore":
        backups = _list_backups()
        if not backups:
            return jsonify({"error": f"no backups found in {config.BACKUP_DIR} — run db-restore.sh --pull on the host"}), 404
        date = body.get("date", "")
        available_dates = [b["date"] for b in backups]
        if not date or date == "latest":
            date = available_dates[0]
        elif date not in available_dates:
            return jsonify({"error": f"backup {date} not found"}), 404
        threading.Thread(target=_restore_worker, args=(date, config.DB_PASSWORD), daemon=True).start()
        return jsonify({"ok": True, "date": date}), 202

    if action == "wipe":
        threading.Thread(target=_wipe_worker, daemon=True).start()
        return jsonify({"ok": True}), 202

    return jsonify({"error": f"unknown action: {action!r}"}), 400


@app.route("/healthz")
def healthz():
    return "ok", 200


def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app.run(host="0.0.0.0", port=config.MONITOR_PORT, threaded=True)


if __name__ == "__main__":
    main()
