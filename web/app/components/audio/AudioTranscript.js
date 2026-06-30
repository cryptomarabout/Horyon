"use client";

import { useState, useEffect, useMemo } from "react";
import { useAudio } from "../../../lib/audioContext";

// ── Script parsing ─────────────────────────────────────────────────────────────
// The stored script is a flat dialogue (HOST:/EXPERT: or real names like Maya:/Daniel:) —
// chapter markers are stripped at generation, so chapter titles + start times live ONLY in
// the `chapters` JSONB (driven below), NOT in the script text.
// First unique speaker → "host" CSS role, second → "expert".
function parseTurns(script) {
  const turns = [];
  const roleMap = {};
  const roles = ["host", "expert"];

  function roleFor(name) {
    const key = name.toUpperCase();
    if (key === "HOST") return "host";
    if (key === "EXPERT") return "expert";
    if (!(key in roleMap)) roleMap[key] = roles[Object.keys(roleMap).length] || "expert";
    return roleMap[key];
  }

  for (const raw of (script || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    // Drop any stray chapter/heading markers — they're not spoken.
    if (/^#{1,4}\s+/.test(line)) continue;
    const speakM = line.match(/^([A-Za-z][A-Za-z0-9]*)\s*[:\-–]\s*(.*)/);
    if (speakM) {
      const name = speakM[1];
      const role = roleFor(name);
      const text = speakM[2].trim();
      if (!text) continue;
      const last = turns[turns.length - 1];
      if (last && last.role === role) { last.text += " " + text; }
      else { turns.push({ speaker: name, role, text }); }
      continue;
    }
    const last = turns[turns.length - 1];
    if (last) last.text += " " + line;
  }
  return turns;
}

function sentences(text) {
  return text.match(/[^.!?]+[.!?]*/g)?.map(s => s.trim()).filter(Boolean) || [text];
}

// ── Component ──────────────────────────────────────────────────────────────────
export default function AudioTranscript({ date, variant }) {
  const { cur, chaps, activeChap, hasChapters, active, jumpTo } = useAudio();
  const dur = active?.duration_sec || 0;

  const [script, setScript] = useState(null);
  const [status, setStatus] = useState("idle");

  useEffect(() => {
    if (!date || !variant) return;
    setStatus("loading");
    setScript(null);
    fetch(`/api/audio/${date}/transcript?variant=${variant}`)
      .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(data => { setScript(data.script || ""); setStatus("ready"); })
      .catch(() => setStatus("error"));
  }, [date, variant]);

  // Flat sentence list + per-sentence time windows. TTS duration tracks character count far
  // better than sentence count, so we weight each sentence's slice of the timeline by its
  // length. windows[i] = [start, end) seconds for sentence i.
  const { flat, windows } = useMemo(() => {
    const turns = parseTurns(script);
    const flat = turns.flatMap(turn =>
      sentences(turn.text).map(s => ({ text: s, speaker: turn.speaker, role: turn.role, len: s.length }))
    );
    const total = flat.reduce((n, s) => n + s.len, 0) || 1;
    const windows = [];
    let acc = 0;
    for (const s of flat) {
      const start = (acc / total) * dur;
      acc += s.len;
      windows.push([start, (acc / total) * dur]);
    }
    return { flat, windows };
  }, [script, dur]);

  if (status === "loading") return (
    <div className="audio-transcript"><div className="audio-tx-status">Loading…</div></div>
  );
  if (status === "error") return (
    <div className="audio-transcript"><div className="audio-tx-status audio-tx-status--err">Transcript unavailable</div></div>
  );
  if (script == null || flat.length === 0) return null;

  // Active sentence = the one whose char-weighted time window contains the playhead.
  const live = cur > 0 && dur > 0;
  let spotIdx = -1;
  if (live) {
    spotIdx = windows.findIndex(([s, e]) => cur >= s && cur < e);
    if (spotIdx < 0) spotIdx = cur >= dur ? flat.length - 1 : 0;
  }

  // Subtitle window: one line before, current line, one line after.
  const prev = spotIdx > 0 ? flat[spotIdx - 1] : null;
  const curr = spotIdx >= 0 ? flat[spotIdx] : flat[0] || null;
  const next = spotIdx >= 0 && spotIdx < flat.length - 1 ? flat[spotIdx + 1] : null;

  // Chapter context comes from the `chapters` JSONB (the real source of truth), not the script.
  const chapTitle = hasChapters && activeChap >= 0 ? chaps[activeChap]?.title : null;

  return (
    <div className="audio-transcript">
      {/* Chapter strip — driven by the chapters JSONB; jumps move the audio. */}
      {hasChapters && (
        <div className="audio-tx-nav">
          <button
            className="audio-tx-nav-btn"
            disabled={activeChap <= 0}
            onClick={() => activeChap > 0 && jumpTo(chaps[activeChap - 1].start)}
            aria-label="Previous chapter"
          >‹</button>
          <span className="audio-tx-nav-loc">
            <span className="audio-tx-nav-n">{Math.max(1, activeChap + 1)}</span>
            <span className="audio-tx-nav-sep"> / </span>
            <span className="audio-tx-nav-total">{chaps.length}</span>
            {chapTitle && <span className="audio-tx-nav-title">{chapTitle}</span>}
          </span>
          <button
            className="audio-tx-nav-btn"
            disabled={activeChap >= chaps.length - 1}
            onClick={() => activeChap < chaps.length - 1 && jumpTo(chaps[activeChap + 1].start)}
            aria-label="Next chapter"
          >›</button>
          {live && <span className="audio-tx-live" aria-label="Live">●</span>}
        </div>
      )}

      {/* Subtitle view — 3-line window (prev · current · next) */}
      <div className="audio-subtitle">
        <div className="audio-sub-line audio-sub-line--prev" aria-hidden>
          {prev ? (
            <>
              <span className={`audio-sub-speaker audio-sub-speaker--${prev.role}`}>{prev.speaker}</span>
              <span className="audio-sub-text">{prev.text}</span>
            </>
          ) : null}
        </div>

        <div className={`audio-sub-line audio-sub-line--curr${!live ? " audio-sub-line--idle" : ""}`}>
          {live && curr ? (
            <>
              <span className={`audio-sub-speaker audio-sub-speaker--${curr.role}`}>{curr.speaker}</span>
              <span className="audio-sub-text">{curr.text}</span>
            </>
          ) : (
            <span className="audio-sub-hint">▶ Press play to follow along</span>
          )}
        </div>

        <div className="audio-sub-line audio-sub-line--next" aria-hidden>
          {next ? (
            <>
              <span className={`audio-sub-speaker audio-sub-speaker--${next.role}`}>{next.speaker}</span>
              <span className="audio-sub-text">{next.text}</span>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
