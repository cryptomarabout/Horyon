// app/lib/db/audio.js — audio-briefing metadata
import { unstable_cache } from "next/cache";
import { safeRows } from "./_core.js";


// Audio-briefing METADATA for a date — ALL rendered length variants (short/standard/explainer),
// never the bytes (those stream via /api/audio/[date]?variant=). Returns { variants: [...] }
// ordered shortest→longest, or null if none exist. Resilient: returns null if the table/column
// doesn't exist yet (web deployed before the migration).
// Cached 10 min per date — new audio variants can be generated post-digest within the hour.
export function getAudioBriefing(date) {
  return unstable_cache(
    async () => {
      const rows = await safeRows(
        `SELECT variant,
                to_char(digest_date,'YYYY-MM-DD') AS date,
                mime, voice, tts_engine, duration_sec, byte_size, word_count, status,
                chapters, waveform, (audio IS NOT NULL) AS has_audio
         FROM digest_audio
         WHERE digest_date = $1::date
         ORDER BY CASE variant
                    WHEN 'short' THEN 0 WHEN 'standard' THEN 1 WHEN 'explainer' THEN 2 ELSE 3
                  END`,
        [date]
      );
      return rows.length ? { variants: rows } : null;
    },
    ["horyon-audio-briefing", date],
    { revalidate: 600 }
  )();
}
