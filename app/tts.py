"""Text-to-speech for the daily audio briefing — a thin, swappable engine interface.

``synthesize(text) -> (audio_bytes, mime, engine_name)`` is the only thing callers touch, so the
engine choice never leaks into the briefing pipeline and is a one-line config change.

MVP engine: **Edge TTS** (Microsoft neural voices via the free ``edge-tts`` package). Zero local
compute (it's a cloud call — which matters on this 2-core host), native MP3 out (no ffmpeg), and
genuinely good quality. It rides an UNOFFICIAL Microsoft endpoint, so it *can* break: set
``TTS_FALLBACK_ENGINE='piper'`` for a self-hosted fallback once Piper + a voice model are
installed. Both are free; audio is best-effort and never blocks the digest, so a total failure
just means no audio that day.
"""
from __future__ import annotations

import asyncio
import io
import logging
import subprocess
import threading

from . import config

log = logging.getLogger(__name__)


def _run_async(coro_factory, timeout: float):
    """Run an async coroutine to completion on a private event loop in a worker thread, with a
    hard timeout. Works whether or not the caller already has a running loop (our cron/CLI paths
    don't, but this stays robust either way) and surfaces the coroutine's exception."""
    box: dict = {}

    def runner():
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro_factory())
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread below
            box["error"] = exc
        finally:
            loop.close()

    t = threading.Thread(target=runner, name="tts-synth", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"TTS timed out after {timeout}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


# ── Engines ──────────────────────────────────────────────────────────────────
async def _edge_collect(text: str, voice: str, rate: str) -> bytes:
    import edge_tts  # lazy: optional dep, only needed when the edge engine actually runs

    kwargs = {"voice": voice}
    if rate:
        kwargs["rate"] = rate
    communicate = edge_tts.Communicate(text, **kwargs)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            buf.extend(chunk["data"])
    if not buf:
        raise RuntimeError("edge-tts returned no audio")
    return bytes(buf)


def _edge_synthesize(text: str, voice: str) -> tuple[bytes, str]:
    audio = _run_async(lambda: _edge_collect(text, voice, config.TTS_RATE), config.TTS_TIMEOUT_SEC)
    return audio, "audio/mpeg"


def _piper_synthesize(text: str, voice: str) -> tuple[bytes, str]:
    # voice here is the .onnx model path; the configured PIPER_VOICE wins (Edge voice ids don't apply).
    model = config.PIPER_VOICE or (voice if voice.endswith(".onnx") else "")
    if not model:
        raise RuntimeError("PIPER_VOICE (.onnx model path) not configured")
    proc = subprocess.run(
        [config.PIPER_BINARY, "--model", model, "--output_file", "-"],
        input=text.encode("utf-8"), capture_output=True, timeout=config.TTS_TIMEOUT_SEC,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"piper failed: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout, "audio/wav"


_ENGINES = {"edge": _edge_synthesize, "piper": _piper_synthesize}


def synthesize(text: str, voice: str | None = None) -> tuple[bytes, str, str]:
    """Render ``text`` to audio. Tries ``TTS_ENGINE`` then ``TTS_FALLBACK_ENGINE`` (if set and
    distinct). Returns (audio_bytes, mime, engine_name); raises if every engine fails."""
    voice = voice or config.TTS_VOICE
    order = [config.TTS_ENGINE]
    if config.TTS_FALLBACK_ENGINE and config.TTS_FALLBACK_ENGINE != config.TTS_ENGINE:
        order.append(config.TTS_FALLBACK_ENGINE)

    last_err: Exception | None = None
    for name in order:
        fn = _ENGINES.get(name)
        if not fn:
            log.warning("tts: unknown engine %r — skipping", name)
            continue
        try:
            audio, mime = fn(text, voice)
            log.info("tts: synthesized %d bytes via %s", len(audio), name)
            return audio, mime, name
        except Exception as exc:  # noqa: BLE001 — try the next engine, report the last failure
            last_err = exc
            log.warning("tts: engine %s failed: %s", name, exc, exc_info=True)
    raise RuntimeError(f"all TTS engines failed: {last_err}")


# ── Silent MP3 padding ────────────────────────────────────────────────────────
# A single silent MPEG-2 Layer III frame matching exactly what edge-tts emits (24 kHz, mono,
# 48 kbps — confirmed via `file` on real output: "MPEG ADTS, layer III, v2, 48 kbps, 24 kHz,
# Monaural"). Header bytes FF F3 64 C0 encode: sync + MPEG-2 + Layer III + no-CRC + 48 kbps + 24 kHz
# + mono; the 140 zero bytes that follow are side-info + main-data = pure silence. One frame carries
# 576 samples = 576/24000 s = 24 ms. Concatenating these between turns is clean because the format
# is identical to the spoken frames (same trick edge uses to stitch its own output).
_SILENCE_FRAME = bytes([0xFF, 0xF3, 0x64, 0xC0]) + bytes(140)
_SILENCE_FRAME_MS = 24.0


def _silence_mp3(ms: float) -> tuple[bytes, float]:
    """``ms`` of silence as whole 24 ms MP3 frames → (bytes, actual_ms). Rounds to the nearest
    frame; returns (b'', 0.0) for a non-positive request."""
    n = max(0, round((ms or 0) / _SILENCE_FRAME_MS))
    return _SILENCE_FRAME * n, n * _SILENCE_FRAME_MS


def synthesize_dialogue(turns: "list[tuple]", voices: dict, *, turn_gap_ms: "int | None" = None,
                        story_gap_ms: "int | None" = None) -> tuple[bytes, str, str, "list[float]"]:
    """Render a multi-speaker dialogue to a single audio stream.

    ``turns`` is ``[(speaker, text[, chapter]), …]`` in order; ``voices`` maps each speaker label to
    a voice id. Each turn is synthesized with its speaker's voice and the streams are concatenated —
    this works because edge-tts emits HEADERLESS MP3 frames that join cleanly into one playable file.

    A short SILENCE is inserted between turns so the conversation breathes like a real podcast: a
    ``turn_gap_ms`` beat between back-to-back turns, and a longer ``story_gap_ms`` breath when the
    next turn opens a NEW chapter (3rd tuple element changes) — that is the missing pause when the
    host jumps from one topic to an unrelated one. Silence is MP3-only (the edge path); a WAV
    fallback gets none. Each gap is folded into the PRECEDING turn's reported duration so the
    returned per-turn timings (and thus chapter timestamps) stay accurate.

    Edge-only by design (piper is single-voice). To stay safe, every turn must come back from the
    SAME engine with the SAME mime; if a turn falls back to a different engine (e.g. piper WAV),
    naive concatenation would corrupt the file, so we refuse and raise — the caller then falls back
    to a single-voice monologue (status stays best-effort).

    Returns ``(audio_bytes, mime, engine, durations)`` where ``durations`` is one whole-second
    estimate per INPUT turn (0.0 for an empty/skipped turn), index-aligned with ``turns`` so the
    caller can map chapter markers to timestamps."""
    turn_gap_ms = config.TTS_TURN_GAP_MS if turn_gap_ms is None else turn_gap_ms
    story_gap_ms = config.TTS_STORY_GAP_MS if story_gap_ms is None else story_gap_ms
    parts: list[bytes] = []
    durations: list[float] = []
    engine_used: str | None = None
    mime_used: str | None = None
    prev_chapter = None
    last_spoken_idx = -1  # index in `durations` of the last non-empty turn (to absorb a trailing gap)
    for idx, turn in enumerate(turns):
        speaker, text = turn[0], turn[1]
        chapter = turn[2] if len(turn) > 2 else None
        text = (text or "").strip()
        if not text:
            durations.append(0.0)
            continue
        audio, mime, engine = synthesize(text, voice=voices.get(speaker) or config.TTS_VOICE)
        if mime_used and (mime != mime_used or engine != engine_used):
            raise RuntimeError(
                f"dialogue cannot concatenate mixed engines ({engine_used}/{mime_used} vs "
                f"{engine}/{mime}) — falling back to single-voice")
        # Pause BEFORE this turn (only between two spoken turns, MP3 only). A new chapter → the
        # longer between-story breath; same chapter → the short turn beat.
        if parts and mime == "audio/mpeg":
            new_story = chapter is not None and prev_chapter is not None and chapter != prev_chapter
            sil, sil_ms = _silence_mp3(story_gap_ms if new_story else turn_gap_ms)
            if sil:
                parts.append(sil)
                if last_spoken_idx >= 0:
                    durations[last_spoken_idx] += sil_ms / 1000.0
        parts.append(audio)
        durations.append(float(estimate_duration(audio, mime) or 0.0))
        last_spoken_idx = len(durations) - 1
        prev_chapter = chapter
        engine_used, mime_used = engine, mime
    if not parts:
        raise RuntimeError("dialogue had no speakable turns")
    log.info("tts: stitched %d dialogue turns via %s (%d bytes)",
             len(durations), engine_used, sum(len(p) for p in parts))
    return b"".join(parts), mime_used or "audio/mpeg", engine_used or config.TTS_ENGINE, durations


_VARIANT_DISPLAY = {"short": "Flash Briefing", "standard": "Daily Briefing", "explainer": "Deep Dive"}


def embed_id3_tags(audio_bytes: bytes, title: str, artist: str = "Horyon",
                   album: str = "Horyon Daily Briefings", date_str: str = "") -> bytes:
    """Prepend ID3v2 tags to a raw MP3 stream so OS media players show rich metadata on the lock
    screen. Best-effort — returns the original bytes untouched if mutagen fails or the stream
    already has an ID3 header (to avoid double-tagging)."""
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TALB, TDRC, TCON
        try:
            ID3(io.BytesIO(audio_bytes))
            return audio_bytes  # already tagged
        except ID3NoHeaderError:
            pass
        tags = ID3()
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TPE1(encoding=3, text=[artist]))
        tags.add(TALB(encoding=3, text=[album]))
        if date_str:
            tags.add(TDRC(encoding=3, text=[date_str]))
        tags.add(TCON(encoding=3, text=["Podcast"]))
        header = io.BytesIO()
        tags.save(header)
        return header.getvalue() + audio_bytes
    except Exception:
        log.debug("tts: id3 tagging failed", exc_info=True)
        return audio_bytes


def compute_waveform(script: str, n_bars: int = 100) -> list[float]:
    """Pseudo-waveform from local word-length variance + deterministic per-position jitter.
    Produces a visually plausible bar array (0-1) without decoding any audio bytes.
    Consistent for the same script content so re-synth doesn't change the waveform shape."""
    import hashlib as _hashlib
    words = (script or "").split()
    if not words:
        return [0.4] * n_bars
    n = len(words)
    chunk = max(1.0, n / n_bars)
    bars: list[float] = []
    seed_key = _hashlib.md5(script[:64].encode("utf-8", "replace")).hexdigest()[:8]
    for i in range(n_bars):
        start = int(i * chunk)
        end = int((i + 1) * chunk)
        local = words[start : min(end, n)]
        if local:
            avg_len = sum(len(w.strip(".,!?:;\"'")) for w in local) / len(local)
            base = 0.2 + min(0.7, (avg_len - 2) / 10.0 * 0.7)
        else:
            base = 0.4
        seed = int(_hashlib.md5(f"{i}:{seed_key}".encode()).hexdigest()[:8], 16)
        jitter = ((seed % 1000) / 1000.0 - 0.5) * 0.35
        bars.append(round(max(0.05, min(1.0, base + jitter)), 3))
    return bars


def estimate_duration(audio_bytes: bytes, mime: str = "audio/mpeg",
                      word_count: int | None = None) -> int | None:
    """Best-effort whole-second duration. Reads container metadata via mutagen (pure-python, no
    ffmpeg); falls back to a word-count estimate (~150 wpm) when that's unavailable.

    Uses the mime-specific mutagen class, not the generic ``File()`` sniffer — edge-tts emits a
    HEADERLESS MP3 stream that ``File()`` fails to identify (returns None), while ``mp3.MP3()``
    parses the frames correctly."""
    try:
        bio = io.BytesIO(audio_bytes)
        if mime == "audio/wav":
            from mutagen.wave import WAVE  # lazy
            info = WAVE(bio).info
        else:
            from mutagen.mp3 import MP3  # lazy
            info = MP3(bio).info
        if info and getattr(info, "length", 0):
            return int(round(info.length))
    except Exception:
        log.debug("tts: mutagen duration probe failed", exc_info=True)
    if word_count:
        return int(round(word_count / 2.5))  # ~150 words/minute
    return None
