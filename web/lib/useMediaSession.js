import { useEffect } from "react";
import { chapterAt, VARIANT_LABELS } from "./audio";

// Wires the OS MediaSession (lock screen, AirPods, car audio) to the briefing
// <audio> element: lock-screen metadata, plus play/pause/seek and prev/next-
// chapter action handlers. Re-registers handlers whenever the chapter set
// changes; clears them on unmount. `audioRef` is the player's <audio> ref.
export default function useMediaSession({ date, variant, hasChapters, chaps, audioRef }) {
  // MediaSession metadata — populates the OS lock screen (title, artist, artwork).
  useEffect(() => {
    if (typeof navigator === "undefined" || !("mediaSession" in navigator)) return;
    let dateLabel = date;
    try {
      const d = new Date(date + "T12:00:00Z");
      dateLabel = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    } catch {}
    navigator.mediaSession.metadata = new MediaMetadata({
      title: `Horyon ${VARIANT_LABELS[variant] || "Briefing"} · ${dateLabel}`,
      artist: "Horyon",
      album: "Crypto Intelligence",
      artwork: [{ src: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
    });
  }, [date, variant]);

  // Action handlers — prev/next chapter, seek, play/pause for OS media controls.
  useEffect(() => {
    if (typeof navigator === "undefined" || !("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    ms.setActionHandler("play", () => audioRef.current?.play().catch(() => {}));
    ms.setActionHandler("pause", () => audioRef.current?.pause());
    ms.setActionHandler("seekbackward", ({ seekOffset = 10 } = {}) => {
      const a = audioRef.current;
      if (a) a.currentTime = Math.max(0, a.currentTime - seekOffset);
    });
    ms.setActionHandler("seekforward", ({ seekOffset = 10 } = {}) => {
      const a = audioRef.current;
      if (a) a.currentTime = Math.min(a.duration || 0, a.currentTime + seekOffset);
    });
    try {
      ms.setActionHandler("seekto", ({ seekTime }) => {
        if (audioRef.current && seekTime != null) audioRef.current.currentTime = seekTime;
      });
    } catch {}
    ms.setActionHandler("previoustrack", () => {
      const a = audioRef.current;
      if (!a) return;
      if (hasChapters) {
        const idx = chapterAt(chaps, a.currentTime);
        a.currentTime =
          idx > 0 && a.currentTime - chaps[idx].start > 3
            ? chaps[idx].start
            : idx > 0
            ? chaps[idx - 1].start
            : 0;
      } else {
        a.currentTime = 0;
      }
    });
    ms.setActionHandler("nexttrack", () => {
      const a = audioRef.current;
      if (!a) return;
      const idx = hasChapters ? chapterAt(chaps, a.currentTime) : -1;
      if (idx >= 0 && idx < chaps.length - 1) a.currentTime = chaps[idx + 1].start;
    });
    return () => {
      for (const action of ["play", "pause", "seekbackward", "seekforward", "previoustrack", "nexttrack"]) {
        try { ms.setActionHandler(action, null); } catch {}
      }
      try { ms.setActionHandler("seekto", null); } catch {}
    };
  }, [hasChapters, chaps, audioRef]);
}
