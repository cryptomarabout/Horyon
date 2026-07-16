"use client";

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useAudio } from "../../lib/audioContext";
import { fmt, parseAudioDeepLink } from "../../lib/audio";
import AudioVariants from "./audio/AudioVariants";
import AudioCollapsed from "./audio/AudioCollapsed";
import AudioChapters from "./audio/AudioChapters";
import AudioTranscript from "./audio/AudioTranscript";
import { PlayIcon, PauseIcon, SkipBackIcon, SkipFwdIcon, ChevronUpIcon } from "./icons";

// The daily-briefing player. Registers its source with AudioProvider (which owns the
// <audio> element + all state) and renders the full transport from context. The player
// lives only on the Daily feed — leaving the route pauses playback (the position is
// persisted, so it resumes where you left off when you return).
export default function AudioPlayer({ date, variants = [], onOpenStory = null }) {
  const {
    variant, active, playing, cur, dur, pct, rate,
    showChapters, showTranscript, hover, expanded, hasVariants, chaps, hasChapters, activeChap,
    audioRef,
    setSource, toggle, seek, skip, jumpTo, requestSeek, cycleRate, switchVariant,
    setShowChapters, setShowTranscript, setHover, setExpanded, setPlayerMounted, trackHover,
    openStory,
  } = useAudio();

  const list = Array.isArray(variants) ? variants.filter(Boolean) : [];
  const searchParams = useSearchParams();
  const deepLinkDone = useRef(false);

  // Chapter deep link (?variant=&t=): once the source for this date is registered, seek to
  // the linked time (switching length first if the link names one). Runs at most once.
  useEffect(() => {
    if (deepLinkDone.current || !active) return;
    const { variant: v, t } = parseAudioDeepLink(searchParams);
    if (t == null && !v) { deepLinkDone.current = true; return; }
    deepLinkDone.current = true;
    if (t != null) requestSeek(t, v);
    else if (v && v !== variant) switchVariant(v);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, searchParams]);

  // Register audio source + open-story callback; signal this UI is mounted.
  // If audio is already playing when we mount (returning to Daily), restore expanded.
  // On unmount (navigating away) pause — the player is Daily-only.
  useEffect(() => {
    setPlayerMounted(true);
    setSource(date, list, onOpenStory);
    if (playing) setExpanded(true);
    return () => {
      setPlayerMounted(false);
      if (audioRef?.current) audioRef.current.pause();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);

  // Keep openStory callback fresh without resetting the source.
  useEffect(() => {
    setSource(date, list, onOpenStory);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onOpenStory]);

  if (!active) return null;

  const effectiveDur = active?.duration_sec || dur;
  const waveform = Array.isArray(active?.waveform) && active.waveform.length > 0
    ? active.waveform
    : null;

  return (
    <div className={`audio-wrap${expanded ? " is-expanded" : ""}`}>
      {!expanded ? (
        <AudioCollapsed
          playing={playing}
          variant={variant}
          cur={cur}
          dur={effectiveDur}
          onToggle={toggle}
          onExpand={() => setExpanded(true)}
        />
      ) : (
        <>
          {/* Variant switcher + collapse chevron share one line. */}
          <div className="audio-topbar">
            {hasVariants && <AudioVariants list={list} variant={variant} onSwitch={switchVariant} />}
            <button
              type="button"
              className="audio-collapse audio-collapse--top"
              onClick={() => setExpanded(false)}
              aria-expanded={true}
              aria-label="Collapse briefing player"
              title="Collapse"
            >
              <ChevronUpIcon size={14} />
            </button>
          </div>

          <div className="audio-player" role="group" aria-label="Briefing playback controls">
            {/* ±15s skip + play/pause */}
            <button className="audio-skip" onClick={() => skip(-15)} aria-label="Skip back 15 seconds">
              <SkipBackIcon size={18} />
            </button>
            <button
              className="audio-play"
              onClick={toggle}
              aria-label={playing ? "Pause briefing" : "Play daily briefing"}
            >
              {playing ? <PauseIcon size={14} /> : <PlayIcon size={13} />}
            </button>
            <button className="audio-skip" onClick={() => skip(15)} aria-label="Skip forward 15 seconds">
              <SkipFwdIcon size={18} />
            </button>

            {/* Waveform or flat progress track */}
            <div
              className={`audio-track${waveform ? " audio-track--wave" : ""}`}
              onClick={seek}
              onMouseMove={trackHover}
              onMouseLeave={() => setHover(null)}
              role="progressbar"
              aria-label="Seek briefing"
              aria-valuenow={Math.round(pct)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              {waveform ? (
                <svg
                  className="audio-waveform-svg"
                  viewBox={`0 0 ${waveform.length} 1`}
                  preserveAspectRatio="none"
                  aria-hidden
                >
                  {waveform.map((h, i) => {
                    const filled = effectiveDur > 0 && (i / waveform.length) < pct / 100;
                    return (
                      <rect
                        key={i}
                        x={i + 0.1}
                        y={1 - h}
                        width={0.8}
                        height={h}
                        fill={filled ? "var(--accent)" : "var(--text-4)"}
                        opacity={filled ? 1 : 0.35}
                      />
                    );
                  })}
                </svg>
              ) : (
                <>
                  <div className="audio-fill" style={{ width: `${pct}%` }} />
                  {hasChapters &&
                    effectiveDur > 0 &&
                    chaps.map((c, i) =>
                      i === 0 ? null : (
                        <span
                          key={i}
                          className="audio-tick"
                          style={{ left: `${Math.min(100, (c.start / effectiveDur) * 100)}%` }}
                        />
                      )
                    )}
                </>
              )}
              {hover && (
                <span className="audio-hover-tip" style={{ left: `${hover.x}px` }}>
                  <span className="audio-hover-time">{fmt(hover.start)}</span>
                  {hover.title}
                </span>
              )}
            </div>

            <span className="audio-time">
              {fmt(cur)}&nbsp;/&nbsp;{fmt(effectiveDur)}
            </span>

            {/* Secondary control cluster: chapters · transcript · speed */}
            <div className="audio-ctrl-group">
              {hasChapters && (
                <button
                  className={`audio-chaps-btn${showChapters ? " on" : ""}`}
                  onClick={() => setShowChapters((v) => !v)}
                  aria-label="Chapters"
                  aria-expanded={showChapters}
                  title="Chapters"
                >
                  ≡
                </button>
              )}
              <button
                className={`audio-tx-btn${showTranscript ? " on" : ""}`}
                onClick={() => setShowTranscript((v) => !v)}
                aria-label="Transcript"
                aria-expanded={showTranscript}
                title="Transcript"
              >
                ⊟
              </button>
              <button className="audio-rate" onClick={cycleRate} aria-label="Playback speed">
                {rate}×
              </button>
            </div>
          </div>

          {hasChapters && showChapters && (
            <AudioChapters
              chaps={chaps}
              activeChap={activeChap}
              date={date}
              variant={variant}
              onJump={jumpTo}
              onOpenStory={onOpenStory ? openStory : null}
            />
          )}

          {showTranscript && (
            <AudioTranscript date={date} variant={variant} />
          )}
        </>
      )}
    </div>
  );
}
