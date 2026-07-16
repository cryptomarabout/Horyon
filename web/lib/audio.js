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

// Shareable permalink to a specific chapter of a day's briefing:
//   /d/2026-07-15?variant=explainer&t=312
// AudioPlayer reads ?variant=/?t= on mount and seeks there (parseAudioDeepLink).
export function chapterDeepLink(date, variant, start) {
  const t = Math.max(0, Math.floor(Number(start) || 0));
  const v = VARIANT_LABELS[variant] ? variant : "standard";
  return `/d/${date}?variant=${encodeURIComponent(v)}&t=${t}`;
}

// Parse a player deep link from a URLSearchParams-like object (has .get). Returns
// { variant, t } with variant validated against the known set and t a non-negative
// integer, or null for each when absent/invalid — so a hand-edited ?t=abc is ignored.
export function parseAudioDeepLink(params) {
  if (!params || typeof params.get !== "function") return { variant: null, t: null };
  const vRaw = params.get("variant");
  const variant = vRaw && VARIANT_LABELS[vRaw] ? vRaw : null;
  const tRaw = params.get("t");
  const tNum = tRaw == null ? NaN : Number(tRaw);
  const t = Number.isFinite(tNum) && tNum >= 0 ? Math.floor(tNum) : null;
  return { variant, t };
}

// Index of the chapter that contains time `t` (chapters are sorted ascending by start).
export function chapterAt(chapters, t) {
  let idx = -1;
  for (let i = 0; i < chapters.length; i++) {
    if (t >= chapters[i].start - 0.25) idx = i;
    else break;
  }
  return idx;
}
