"use client";

import { useEffect, useRef, useState } from "react";
import { monogram } from "../../lib/format";

export { monogram };

// Strip trailing version suffixes so "Aave V3" displays as "Aave".
export function baseName(name) {
  return (name || "")
    .replace(/\s+v?\d+(\.\d+)*$/i, "")
    .replace(/\s*\(v?\d+(\.\d+)*\)$/i, "")
    .trim();
}

// Renders avatar cascading through `avatars` URLs; final fallback is a type-coloured monogram.
// imgClass / monoClass let callers match the surrounding chip size.
export function EntityAvatar({ avatars = [], type = "other", name, imgClass = "entity-logo", monoClass }) {
  const [idx, setIdx] = useState(0);
  const imgRef = useRef(null);
  const src = idx < avatars.length ? avatars[idx] : null;

  // An <img> that errors BEFORE React hydration (e.g. a fast same-origin /api/avatar 404)
  // never fires onError — the handler wasn't attached yet — leaving a broken image stuck on
  // screen instead of advancing the cascade. After each src, re-check the real DOM state and
  // advance if the current candidate already failed. (onError still covers post-hydration.)
  useEffect(() => {
    const el = imgRef.current;
    if (el && el.complete && el.naturalWidth === 0) setIdx(i => i + 1);
  }, [src]);

  if (src) {
    return (
      <img ref={imgRef} src={src} alt="" className={imgClass}
        onError={() => setIdx(i => i + 1)} />
    );
  }
  return (
    <span
      className={monoClass || `entity-logo entity-mono entity-mono--${type}`}
      aria-hidden="true"
    >
      {monogram(name)}
    </span>
  );
}

// Dedup entity_memory records by first word (≥4 chars), shortest display name wins.
// Mirrors BulletItem.buildEntities but for the single-source entity_memory case
// (no chain / DeFiLlama three-way merge needed).
export function dedupeEntities(entities) {
  const out = [];
  const byFirst = new Map();
  for (const e of (entities || [])) {
    const display = baseName(e.name);
    const fw = display.toLowerCase().split(/\s+/)[0].replace(/[^a-z0-9]/g, "");
    const hasFw = fw.length >= 4;
    const prevIdx = hasFw ? byFirst.get(fw) : undefined;
    if (prevIdx === undefined) {
      out.push({ ...e, display });
      if (hasFw) byFirst.set(fw, out.length - 1);
    } else {
      const prev = out[prevIdx];
      if (display.length < prev.display.length) {
        out[prevIdx] = { ...e, display };
      }
    }
  }
  return out;
}
