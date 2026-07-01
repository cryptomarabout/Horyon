"""YouTube crypto-podcast ingestion: detect new episodes, fetch transcripts, analyze.

No paid YouTube API. New-episode detection uses the free per-channel RSS feed
(``youtube.com/feeds/videos.xml?channel_id=UC...``); transcripts come from
``youtube-transcript-api`` with a ``yt-dlp`` auto-subtitle fallback.

Per episode the transcript is summarized via a map-reduce LLM pass (chunk → per-chunk
key points → one structured reduce → JSON analysis). The distilled summary is embedded
and the structured analysis feeds the daily digest (see digest.build_digest).

Pipeline (run_once):
  discover_episodes()  → insert 'pending' rows (dedupe on video_id)
  get_pending(...)     → fetch transcript → summarize → store → entity extraction

Best-effort throughout: one episode failing never aborts the batch, and a failed
transcript fetch is retried next cron (status flips pending→failed→…→summarized).

Job: run_once() called every PODCAST_INTERVAL_MIN by APScheduler (see app/main.py).
CLI: python -m app.podcasts [--resolve @handle | --no-persist | --limit N]
"""
from __future__ import annotations

import glob
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import feedparser

from . import config, db, embeddings, entities, http, llm, prompts

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_FENCE_RE = re.compile(r"```[a-z]*\n?", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

# Reasoning / instruction-echo cleanup for map output. Reasoning models sometimes
# emit their chain-of-thought (or an echo of the prompt) instead of the asked-for
# '- ' bullets — that prose must never reach the reduce step or a stored field.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)  # unclosed → drop to end
_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.+)$")
_META_RE = re.compile(
    r"(we need to extract|the user asks?|must be terse|terse bullets|"
    r"plain[- ]?text,?\s*start with|no preamble|\bno extra\b|transcription noise|"
    r"long transcript|must not invent|\blet me (extract|parse|note|summar)|"
    r"i should (extract|note|parse)|from this segment|parse the segment|"
    r"one per line starting|3-?8 (lines|bullets|plain))",
    re.IGNORECASE,
)

# Resolved handle→channelId cache (per-process; channel IDs are immutable).
_channel_id_cache: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# Channel resolution (handle → UC... id)
# --------------------------------------------------------------------------- #
def resolve_channel_id(handle: str) -> str | None:
    """Resolve a channel handle (``@UnchainedCrypto``) to its ``UC...`` id.

    Already-resolved ``UC...`` ids pass through. Scrapes the public channel page
    (no API key). Returns None if it can't be resolved.
    """
    handle = handle.strip()
    if handle.startswith("UC") and len(handle) == 24:
        return handle
    key = handle.lower()
    if key in _channel_id_cache:
        return _channel_id_cache[key]

    h = handle if handle.startswith("@") else "@" + handle
    url = f"https://www.youtube.com/{h}"
    try:
        html = http.fetch(url, ua=_UA, timeout=20).body.decode("utf-8", "ignore")
    except Exception as exc:
        log.warning("channel resolve failed for %s: %s", handle, exc)
        return None

    m = (re.search(r'"(?:channelId|externalId)":"(UC[0-9A-Za-z_-]{22})"', html)
         or re.search(r'<meta itemprop="(?:identifier|channelId)" content="(UC[0-9A-Za-z_-]{22})"', html))
    if not m:
        log.warning("could not find channelId for %s", handle)
        return None
    cid = m.group(1)
    _channel_id_cache[key] = cid
    return cid


# --------------------------------------------------------------------------- #
# Episode detection (free per-channel RSS)
# --------------------------------------------------------------------------- #
def _vid_from_entry(e) -> str | None:
    vid = e.get("yt_videoid")
    if vid:
        return vid
    m = re.search(r"yt:video:([0-9A-Za-z_-]{11})", e.get("id", "") or "")
    return m.group(1) if m else None


def _pub_iso(e) -> str | None:
    st = e.get("published_parsed") or e.get("updated_parsed")
    if not st:
        return None
    return datetime(*st[:6], tzinfo=timezone.utc).isoformat()


def discover_episodes() -> list[dict]:
    """Read each configured channel's RSS feed → list of episode dicts (no DB write)."""
    eps: list[dict] = []
    for handle in config.PODCAST_CHANNELS:
        cid = resolve_channel_id(handle)
        if not cid:
            continue
        try:
            feed = feedparser.parse(_RSS.format(cid))
        except Exception:
            log.warning("RSS parse failed for %s", handle, exc_info=True)
            continue
        for e in feed.entries:
            vid = _vid_from_entry(e)
            if not vid:
                continue
            pub = _pub_iso(e)
            # Cutoff: skip episodes older than PODCAST_MIN_DATE (ISO dates sort
            # lexicographically, so a YYYY-MM-DD prefix compare is correct).
            if config.PODCAST_MIN_DATE and pub and pub[:10] < config.PODCAST_MIN_DATE:
                continue
            eps.append({
                "video_id": vid,
                "channel": handle.lstrip("@"),
                "channel_id": cid,
                "title": e.get("title", "") or "",
                "url": e.get("link") or f"https://www.youtube.com/watch?v={vid}",
                "published_at": pub,
            })
    return eps


# --------------------------------------------------------------------------- #
# Transcript fetch (no paid API): youtube-transcript-api → yt-dlp fallback
# --------------------------------------------------------------------------- #
_LANGS = ["en", "en-US", "en-GB"]


def _short(exc: Exception) -> str:
    """First line of an exception message — YouTube's block error is a wall of text."""
    return (str(exc).splitlines() or [""])[0][:160]


def _via_transcript_api(video_id: str) -> tuple[str, str] | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    proxies = ({"http": config.PODCAST_PROXY, "https": config.PODCAST_PROXY}
               if config.PODCAST_PROXY else None)
    try:
        if proxies is not None:
            snippets = YouTubeTranscriptApi.get_transcript(video_id, languages=_LANGS, proxies=proxies)
        else:
            # Library API changed across versions: try instance .fetch then classmethod.
            try:
                snippets = list(YouTubeTranscriptApi().fetch(video_id, languages=_LANGS))
            except (AttributeError, TypeError):
                snippets = YouTubeTranscriptApi.get_transcript(video_id, languages=_LANGS)
    except Exception as exc:
        log.info("transcript-api miss for %s: %s", video_id, _short(exc))
        return None
    parts = []
    for s in snippets:
        txt = getattr(s, "text", None)
        if txt is None and isinstance(s, dict):
            txt = s.get("text", "")
        if txt:
            parts.append(txt)
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return (text, "en") if text else None


def _parse_vtt(raw: str) -> str:
    """Flatten a WebVTT caption file to plain text, dropping timing + duplicate lines."""
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        if re.match(r"^(Kind|Language):", line):
            continue
        line = _TAG_RE.sub("", line).strip()  # inline <00:00:00.000><c> tags
        if line and (not out or out[-1] != line):
            out.append(line)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def _via_ytdlp(video_id: str) -> tuple[str, str] | None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "%(id)s.%(ext)s")
        cmd = [
            sys.executable, "-m", "yt_dlp", "--skip-download",
            # Without a JS runtime yt-dlp can't resolve video formats ("Only images
            # available") and aborts before writing subs — this makes that non-fatal
            # so subtitle-only extraction proceeds (captions don't need the n-challenge).
            "--ignore-no-formats-error",
            "--write-auto-subs", "--write-subs", "--sub-langs", "en.*",
            "--sub-format", "vtt", "--output", out, url,
        ]
        if config.PODCAST_PROXY:
            cmd += ["--proxy", config.PODCAST_PROXY]
        if config.PODCAST_YTDLP_COOKIES:
            # yt-dlp rewrites the cookie jar on exit. Copy to a writable temp path so
            # the mounted (read-only) source cookies file is never modified/clobbered.
            cj = os.path.join(td, "cookies.txt")
            try:
                shutil.copyfile(config.PODCAST_YTDLP_COOKIES, cj)
                cmd += ["--cookies", cj]
            except OSError:
                cmd += ["--cookies", config.PODCAST_YTDLP_COOKIES]
        try:
            subprocess.run(cmd, capture_output=True, timeout=180, check=False)
        except Exception as exc:
            log.info("yt-dlp miss for %s: %s", video_id, exc)
            return None
        vtts = sorted(glob.glob(os.path.join(td, "*.vtt")))
        if not vtts:
            return None
        try:
            with open(vtts[0], encoding="utf-8") as f:
                text = _parse_vtt(f.read())
        except OSError:
            return None
    return (text, "en") if text else None


def fetch_transcript(video_id: str) -> tuple[str, str] | None:
    """Return (transcript_text, lang) or None.

    When a cookies/proxy auth path exists for yt-dlp, prefer it — the bare
    `youtube-transcript-api` is IP-blocked on this host and would just waste a
    failed request. Otherwise try the light API first, then yt-dlp.
    """
    if config.PODCAST_YTDLP_COOKIES or config.PODCAST_PROXY:
        return _via_ytdlp(video_id) or _via_transcript_api(video_id)
    return _via_transcript_api(video_id) or _via_ytdlp(video_id)


# --------------------------------------------------------------------------- #
# Map-reduce summarization
# --------------------------------------------------------------------------- #
def _chunk(text: str) -> list[str]:
    """Split transcript into word-aligned chunks, capped at PODCAST_MAX_MAP_CALLS."""
    size = config.PODCAST_CHUNK_CHARS
    cap = config.PODCAST_MAX_MAP_CALLS
    if len(text) > size * cap:  # very long episode → grow chunks to stay within the cap
        size = math.ceil(len(text) / cap)
    chunks, cur, cur_len = [], [], 0
    for w in text.split():
        cur.append(w)
        cur_len += len(w) + 1
        if cur_len >= size:
            chunks.append(" ".join(cur))
            cur, cur_len = [], 0
    if cur:
        chunks.append(" ".join(cur))
    return chunks[:cap]


def _clean_map_note(content: str) -> str:
    """Keep only the '- ' bullets a map call was asked to produce.

    Strips <think> blocks, keeps lines starting with a bullet marker, and drops any
    bullet that still reads like instruction-echo / reasoning. Returns '' if nothing
    usable remains (caller then skips that chunk) — this is what stops a reasoning
    model's chain-of-thought from leaking into the reduce + the stored analysis.
    """
    if not content:
        return ""
    text = _THINK_OPEN_RE.sub("", _THINK_RE.sub("", content))
    bullets: list[str] = []
    for line in text.splitlines():
        m = _BULLET_RE.match(line)
        if not m:
            continue
        item = m.group(1).strip()
        if item and not _META_RE.search(item):
            bullets.append("- " + item)
    return "\n".join(bullets)


def _parse_analysis(raw: str, fallback_notes: list[str]) -> dict:
    """Parse the reduce JSON; degrade gracefully so we never infinitely retry."""
    data: dict = {}
    try:
        parsed = llm.parse_json_loose(_THINK_RE.sub("", raw or ""))
        if isinstance(parsed, dict):
            data = parsed
    except (ValueError, TypeError):
        log.warning("podcast reduce: invalid JSON (%.120r)", raw)

    def _list(key: str) -> list[str]:
        v = data.get(key)
        if not isinstance(v, list):
            return []
        return [s for s in (str(x).strip() for x in v) if s and not _META_RE.search(s)][:8]

    tldr = str(data.get("tldr") or "").strip()
    if _META_RE.search(tldr):
        tldr = ""
    if not tldr:
        # Fallback to the (already bullet-cleaned) map notes with markers stripped.
        clean = re.sub(r"(?m)^\s*[-*•]\s*", "", "\n".join(fallback_notes))
        tldr = re.sub(r"\s+", " ", clean).strip()[:600]
    sentiment = str(data.get("sentiment") or "mixed").strip().lower()
    if sentiment not in ("bullish", "bearish", "neutral", "mixed"):
        sentiment = "mixed"
    return {
        "tldr": tldr,
        "themes": _list("themes"),
        "notable_claims": _list("notable_claims"),
        "predictions": _list("predictions"),
        "entities": _list("entities"),
        "guests": _list("guests"),
        "sentiment": sentiment,
    }


def summarize_episode(title: str, channel: str, transcript: str) -> tuple[dict, str]:
    """Map-reduce a transcript into a structured analysis dict + model_used."""
    chunks = _chunk(transcript)
    notes: list[str] = []
    for i, ch in enumerate(chunks):
        try:
            content, _ = llm.complete(prompts.PODCAST_MAP_SYSTEM, ch, max_tokens=600, temperature=0.3)
        except Exception:
            log.warning("podcast map call %d/%d failed", i + 1, len(chunks), exc_info=True)
            continue
        note = _clean_map_note(content)  # bullets-only; drops reasoning/instruction-echo
        if note:
            notes.append(note)
        elif content.strip():
            log.info("podcast map call %d/%d: no usable bullets (likely reasoning leak)", i + 1, len(chunks))
        if i < len(chunks) - 1:  # no need to wait after the final chunk
            time.sleep(config.PODCAST_MAP_DELAY_SEC)
    if not notes:
        raise RuntimeError("no usable map notes (all chunks empty or reasoning-only)")

    user = prompts.build_podcast_reduce_user(title, channel, notes)
    raw, model = llm.complete(prompts.PODCAST_REDUCE_SYSTEM, user, max_tokens=1200,
                              temperature=0.2, json_mode=True)
    return _parse_analysis(raw, notes), model


# --------------------------------------------------------------------------- #
# Per-episode orchestration
# --------------------------------------------------------------------------- #
def _embed_summary(analysis: dict) -> list[float] | None:
    parts = [analysis.get("tldr", "")] + analysis.get("themes", []) + analysis.get("notable_claims", [])
    text = embeddings.clean_for_embedding(" . ".join(p for p in parts if p))
    if not text:
        return None
    try:
        return embeddings.embed(text)
    except Exception:
        log.debug("podcast summary embed failed", exc_info=True)
        return None


def _extract_entities(ep: dict, analysis: dict) -> None:
    """Feed the distilled analysis through the shared ingest-time entity extractor.

    Passes each claim/entity-line as a separate item — the extractor truncates each
    snippet to ~250 chars, so splitting preserves more signal than one blob.
    """
    pub = ep.get("published_at")
    items: list[dict] = []
    if analysis.get("tldr"):
        items.append({"content": analysis["tldr"], "mentions": [], "pub_date": pub})
    for c in analysis.get("notable_claims", []):
        items.append({"content": c, "mentions": [], "pub_date": pub})
    if analysis.get("entities"):
        items.append({"content": "Entities discussed: " + ", ".join(analysis["entities"]),
                      "mentions": [], "pub_date": pub})
    if items:
        entities.extract_and_upsert_entities(items)


def process_episode(ep: dict) -> bool:
    """Fetch transcript → summarize → persist for one pending episode. Returns success."""
    vid = ep["video_id"]
    tr = fetch_transcript(vid)
    if not tr or not tr[0]:
        db.mark_podcast_episode(vid, "failed", "no transcript available")
        return False
    text, lang = tr
    if len(text) < config.PODCAST_MIN_TRANSCRIPT_CHARS:
        db.mark_podcast_episode(vid, "skipped", f"transcript too short ({len(text)} chars)")
        return False

    try:
        analysis, model = summarize_episode(ep.get("title", ""), ep.get("channel", ""), text)
    except Exception as exc:
        log.warning("podcast summarize failed for %s: %s", vid, exc, exc_info=True)
        db.mark_podcast_episode(vid, "failed", f"summarize error: {exc}"[:500])
        return False

    emb = _embed_summary(analysis)
    db.store_podcast_summary(vid, text, lang, None, analysis.get("tldr", ""), analysis, model, emb)

    try:
        _extract_entities(ep, analysis)
    except Exception:
        log.debug("podcast entity extraction failed (non-fatal)", exc_info=True)

    log.info("podcast summarized: %r (%s, model=%s)", ep.get("title", "")[:60], vid, model)
    return True


def run_once(persist: bool = True, max_episodes: int | None = None) -> dict:
    """Detect new episodes, then transcribe+analyze a bounded batch of pending ones."""
    max_episodes = max_episodes or config.PODCAST_MAX_EPISODES_PER_RUN
    discovered = discover_episodes()

    new = 0
    if persist:
        for ep in discovered:
            try:
                if db.insert_podcast_episode(ep):
                    new += 1
            except Exception:
                log.warning("failed to insert episode %s", ep.get("video_id"), exc_info=True)

    processed = failed = 0
    pending = db.get_pending_podcast_episodes(limit=max_episodes) if persist else []
    for ep in pending:
        try:
            if process_episode(ep):
                processed += 1
            else:
                failed += 1
        except Exception:
            log.exception("podcast processing crashed for %s", ep.get("video_id"))
            failed += 1
            try:
                db.mark_podcast_episode(ep["video_id"], "failed", "unexpected error")
            except Exception:
                pass

    stats = {"discovered": len(discovered), "new": new, "processed": processed, "failed": failed}
    log.info("podcast run: %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="YouTube crypto-podcast transcript ingestion")
    ap.add_argument("--resolve", metavar="HANDLE", help="Resolve a channel handle to its UC... id and exit")
    ap.add_argument("--no-persist", action="store_true",
                    help="Discover + summarize the first usable episode, print JSON, write nothing")
    ap.add_argument("--limit", type=int, default=None, help="Max episodes to process this run")
    args = ap.parse_args()

    if args.resolve:
        print(resolve_channel_id(args.resolve) or "NOT FOUND")
    elif args.no_persist:
        eps = discover_episodes()
        print(f"discovered {len(eps)} episodes across {len(config.PODCAST_CHANNELS)} channel(s)")
        for ep in eps:
            tr = fetch_transcript(ep["video_id"])
            if tr and tr[0] and len(tr[0]) >= config.PODCAST_MIN_TRANSCRIPT_CHARS:
                print(f"\n=== {ep['title']} ({ep['channel']}) — transcript {len(tr[0])} chars ===")
                analysis, model = summarize_episode(ep["title"], ep["channel"], tr[0])
                print(f"model: {model}")
                print(json.dumps(analysis, indent=2, ensure_ascii=False))
                break
        else:
            print("no episode with a usable transcript found")
    else:
        print(run_once(persist=True, max_episodes=args.limit))
