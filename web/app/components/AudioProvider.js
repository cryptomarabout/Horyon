"use client";

import { useRef, useState, useEffect, useCallback } from "react";
import { AudioContext } from "../../lib/audioContext";
import { RATES, VARIANT_LABELS, chapterAt } from "../../lib/audio";
import useMediaSession from "../../lib/useMediaSession";

const posKey = (date, variant) => `horyon:audiopos:${date}:${variant}`;
const RATE_KEY = "horyon:audiorate";
const SAVE_EVERY = 5; // seconds between localStorage position saves

export default function AudioProvider({ children }) {
  const audioRef = useRef(null);
  const rateRef = useRef(1);
  const wasPlaying = useRef(false);
  const lastSaved = useRef(0);
  const openStoryRef = useRef(null); // callback registered by AudioPlayer/BulletFeed

  const [date, setDate] = useState(null);
  const [variants, setVariants] = useState([]);
  const [variant, setVariant] = useState("standard");
  const [playing, setPlaying] = useState(false);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);
  const [rate, setRate] = useState(1);
  const [showChapters, setShowChapters] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const [hover, setHover] = useState(null);
  const [expanded, setExpanded] = useState(false);
  // True while the full AudioPlayer UI is mounted in BulletFeed so the
  // persistent mini-bar knows to stay hidden.
  const [playerMounted, setPlayerMounted] = useState(false);
  // Deep-link seek target (seconds) from a ?t= URL. Takes precedence over the saved
  // position on the next load, then clears. Set via requestSeek() by AudioPlayer.
  const pendingSeekRef = useRef(null);

  const active = variants.find((v) => v.variant === variant) || variants[0] || null;
  const chaps = Array.isArray(active?.chapters)
    ? active.chapters.filter((c) => c && isFinite(c.start))
    : [];
  const hasChapters = chaps.length > 1;
  const activeChap = hasChapters ? chapterAt(chaps, cur) : -1;
  const hasVariants = variants.length > 1;
  const pct = (active?.duration_sec || dur) > 0 ? (cur / (active?.duration_sec || dur)) * 100 : 0;
  const label =
    hasChapters && activeChap >= 0 ? chaps[activeChap].title : VARIANT_LABELS[variant] || "Briefing";

  // Restore saved playback rate once on mount.
  useEffect(() => {
    const saved = parseFloat(localStorage.getItem(RATE_KEY) || "1");
    if (RATES.includes(saved)) {
      setRate(saved);
      rateRef.current = saved;
    }
  }, []);

  // Apply rate to the audio element and persist it.
  useEffect(() => {
    rateRef.current = rate;
    if (audioRef.current) audioRef.current.playbackRate = rate;
  }, [rate]);

  // On variant or date change: reset clock, reload, reapply rate, restore saved position,
  // resume if we were playing.
  useEffect(() => {
    const a = audioRef.current;
    if (!a || !date) return;
    setCur(0);
    setShowChapters(false);
    setShowTranscript(false);
    setHover(null);
    setDur(active?.duration_sec || 0);
    a.load();
    a.playbackRate = rateRef.current;

    // Seek target on load: a pending deep-link (?t=) wins over the saved position.
    const pending = pendingSeekRef.current;
    pendingSeekRef.current = null;
    const target = pending != null && isFinite(pending)
      ? pending
      : parseFloat(localStorage.getItem(posKey(date, variant)) || "0");
    const autoplayDeepLink = pending != null;
    if (target > 0 && isFinite(target)) {
      const onMeta = () => {
        a.currentTime = Math.min(target, a.duration || 0);
        setCur(a.currentTime);
        a.removeEventListener("loadedmetadata", onMeta);
        // A deliberate deep link should expand + try to play (autoplay may be blocked;
        // failing is fine — the user lands paused at the right spot).
        if (autoplayDeepLink) {
          setExpanded(true);
          a.play().catch(() => {});
        }
      };
      a.addEventListener("loadedmetadata", onMeta);
    }

    if (wasPlaying.current) {
      wasPlaying.current = false;
      a.play().catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variant, date]);

  useMediaSession({ date, variant, hasChapters, chaps, audioRef });

  // ── Actions ────────────────────────────────────────────────────────────────

  // Called by AudioPlayer on mount (from BulletFeed). Same date → don't disrupt playback,
  // just update the openStory callback. New date → reset everything.
  const setSource = useCallback(
    (newDate, newVariants, onOpenStory) => {
      openStoryRef.current = onOpenStory || null;
      if (newDate === date) return;
      const a = audioRef.current;
      if (a && !a.paused) a.pause();
      setPlaying(false);
      setDate(newDate);
      setVariants(newVariants || []);
      const initial =
        (newVariants || []).find((v) => v.variant === "standard")?.variant ||
        (newVariants || [])[0]?.variant ||
        "standard";
      setVariant(initial);
      setCur(0);
      setDur((newVariants || []).find((v) => v.variant === initial)?.duration_sec || 0);
      setExpanded(false);
      setShowChapters(false);
      setShowTranscript(false);
    },
    [date]
  );

  const toggle = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => {});
    else a.pause();
  }, []);

  const seek = useCallback(
    (e) => {
      const a = audioRef.current;
      const d = active?.duration_sec || dur;
      if (!a || !d) return;
      const r = e.currentTarget.getBoundingClientRect();
      const pct2 = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      a.currentTime = pct2 * d;
      setCur(a.currentTime);
    },
    [active, dur]
  );

  const skip = useCallback((delta) => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = Math.max(0, Math.min(a.duration || 0, a.currentTime + delta));
    setCur(a.currentTime);
  }, []);

  const jumpTo = useCallback((t) => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = Math.max(0, t);
    setCur(a.currentTime);
    a.play().catch(() => {});
  }, []);

  const cycleRate = useCallback(() => {
    const next = RATES[(RATES.indexOf(rate) + 1) % RATES.length];
    setRate(next);
    localStorage.setItem(RATE_KEY, String(next));
  }, [rate]);

  const switchVariant = useCallback(
    (v) => {
      if (v === variant || !variants.some((x) => x.variant === v)) return;
      const a = audioRef.current;
      wasPlaying.current = a ? !a.paused : false;
      if (a) a.pause();
      setPlaying(false);
      setVariant(v);
    },
    [variant, variants]
  );

  // Deep-link seek (?t=): if the audio for this date/variant is already loaded, seek now;
  // otherwise stash it so the load effect applies it once metadata arrives. `targetVariant`
  // (optional) switches length first — the switch reloads and consumes the pending seek.
  const requestSeek = useCallback((t, targetVariant = null) => {
    if (!(t >= 0) || !isFinite(t)) return;
    pendingSeekRef.current = t;
    if (targetVariant && targetVariant !== variant &&
        variants.some((x) => x.variant === targetVariant)) {
      switchVariant(targetVariant);   // reload → load effect applies the pending seek
      return;
    }
    const a = audioRef.current;
    if (a && a.readyState >= 1 && (a.duration || 0) > 0) {
      pendingSeekRef.current = null;
      a.currentTime = Math.min(t, a.duration || 0);
      setCur(a.currentTime);
      setExpanded(true);
      a.play().catch(() => {});
    }
  }, [variant, variants, switchVariant]);

  const trackHover = useCallback(
    (e) => {
      const d = active?.duration_sec || dur;
      if (!hasChapters || !d) return;
      const r = e.currentTarget.getBoundingClientRect();
      const x = Math.min(r.width, Math.max(0, e.clientX - r.left));
      const t = (x / r.width) * d;
      const idx = chapterAt(chaps, t);
      const c = idx >= 0 ? chaps[idx] : chaps[0];
      if (c?.title) setHover({ x, title: c.title, start: c.start });
    },
    [hasChapters, active, dur, chaps]
  );

  const openStory = useCallback((title) => {
    if (openStoryRef.current) openStoryRef.current(title);
  }, []);

  // Stop playback and clear the audio source entirely — hides the persistent bar.
  const dismiss = useCallback(() => {
    const a = audioRef.current;
    if (a && !a.paused) a.pause();
    setPlaying(false);
    setDate(null);
    setVariants([]);
    setExpanded(false);
    setShowChapters(false);
    setShowTranscript(false);
  }, []);

  const ctx = {
    // state
    date, variants, variant, active, playing, cur, dur, rate, pct, label,
    showChapters, showTranscript, hover, expanded, playerMounted,
    hasVariants, chaps, hasChapters, activeChap,
    audioRef,
    // actions
    setSource, toggle, seek, skip, jumpTo, requestSeek, cycleRate, switchVariant,
    setShowChapters, setShowTranscript, setHover, setExpanded, setPlayerMounted, trackHover,
    openStory, dismiss,
  };

  return (
    <AudioContext.Provider value={ctx}>
      {/* Hidden audio element — lives at layout level so it survives route changes. */}
      {date && (
        <audio
          ref={audioRef}
          src={`/api/audio/${date}?variant=${variant}`}
          preload="none"
          style={{ display: "none" }}
          onPlay={() => {
            setPlaying(true);
            setExpanded(true);
            if (navigator?.mediaSession) navigator.mediaSession.playbackState = "playing";
          }}
          onPause={() => {
            setPlaying(false);
            if (navigator?.mediaSession) navigator.mediaSession.playbackState = "paused";
          }}
          onEnded={() => {
            setPlaying(false);
            if (date && variant) localStorage.removeItem(posKey(date, variant));
            if (navigator?.mediaSession) navigator.mediaSession.playbackState = "paused";
          }}
          onTimeUpdate={(e) => {
            const t = e.currentTarget.currentTime;
            setCur(t);
            if (date && variant && Math.abs(t - lastSaved.current) >= SAVE_EVERY) {
              localStorage.setItem(posKey(date, variant), String(t));
              lastSaved.current = t;
            }
            try {
              const d = active?.duration_sec || dur;
              if (navigator?.mediaSession?.setPositionState && d > 0) {
                navigator.mediaSession.setPositionState({
                  duration: d,
                  playbackRate: rateRef.current,
                  position: t,
                });
              }
            } catch {}
          }}
          onLoadedMetadata={(e) => {
            const d = e.currentTarget.duration;
            if (d && isFinite(d)) setDur(d);
          }}
        />
      )}
      {children}
    </AudioContext.Provider>
  );
}
