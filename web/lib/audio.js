// Pure helpers + constants for the daily-briefing audio player.

// "m:ss" elapsed/duration label.
export function fmt(s) {
  if (s == null || Number.isNaN(s) || !isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const ss = Math.floor(s % 60);
  return `${m}:${String(ss).padStart(2, "0")}`;
}

// Approximate length label for a variant tab ("~1 min" / "~6 min" / "~12 min").
export function fmtLen(s) {
  if (!s || !isFinite(s)) return "";
  return `~${Math.max(1, Math.round(s / 60))} min`;
}

export const RATES = [1, 1.25, 1.5, 2];

// Server variant keys → human labels for the length switcher.
export const VARIANT_LABELS = { short: "Flash", standard: "Briefing", explainer: "Deep Dive" };

// Index of the chapter that contains time `t` (chapters are sorted ascending by start).
export function chapterAt(chapters, t) {
  let idx = -1;
  for (let i = 0; i < chapters.length; i++) {
    if (t >= chapters[i].start - 0.25) idx = i;
    else break;
  }
  return idx;
}
