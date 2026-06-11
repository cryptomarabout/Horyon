"""Monitoring dashboard (Flask). Shows container status, ingestion stats, digest
history, keyword analysis history, and per-source health (Twitter vs News).
Served behind Caddy at /monitor. All times displayed in UTC+2.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template_string

from . import config, db, embeddings
from .feeds import SOURCES

log = logging.getLogger(__name__)
app = Flask(__name__)

UTC2 = timezone(timedelta(hours=2))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _fmt(dt, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a datetime in UTC+2."""
    if not dt:
        return "—"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(UTC2).strftime(fmt)
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

    return {
        "now": datetime.now(timezone.utc).astimezone(UTC2).strftime("%Y-%m-%d %H:%M:%S UTC+2"),
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
    }


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
CONTENT = """
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
