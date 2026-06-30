"use client";

import { fmtAgo } from "../../lib/format";
import { ExtIcon } from "./icons";
import { EntityAvatar, baseName } from "./EntityTag";

function absTime(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    timeZone: "UTC", hour12: false,
  }) + " UTC";
}

// ── Entity helpers ─────────────────────────────────────────────────────────
const MAX_ENTITIES = 5;

// Merge chains + DeFiLlama protocols + entity_memory tags into ONE ranked list,
// deduped by exact display name and by first word (≥4 chars) with the SHORTEST
// display winning. Shortest-wins collapses sub-product / piggyback names onto the
// clean parent ACROSS sources.
const KIND_RANK = { chain: 0, proto: 1, mem: 2 };

function pickBetter(a, b) {
  if (a.display.length !== b.display.length) return a.display.length < b.display.length ? a : b;
  const aHas = (a.avatars?.length || 0) > 0, bHas = (b.avatars?.length || 0) > 0;
  if (aHas !== bHas) return aHas ? a : b;
  return KIND_RANK[a.kind] <= KIND_RANK[b.kind] ? a : b;
}

function buildEntities(chains, protocols, entityTags = []) {
  const cand = [
    ...chains.map(c => ({
      kind: "chain", key: `c:${c.name}`, name: c.name, display: c.name, type: "chain",
      avatars: [c.logo || `https://icons.llamao.fi/icons/chains/rsz_${c.name.toLowerCase()}.jpg`],
      url:  c.url || null,
    })),
    ...protocols.map(p => ({
      kind: "proto", key: `p:${p.name}`, name: p.name, display: baseName(p.name), type: "protocol",
      // DeFiLlama serves a reliable CDN logo for every protocol (no unavatar, no rate-limit);
      // entity_avatars is keyed by entity_memory slug, not DeFiLlama slug, so /api/avatar
      // would just 404 here. logo_url → monogram is the right chain for protocols.
      avatars: [p.logo_url].filter(Boolean),
      url:  p.url || (p.slug ? `https://defillama.com/protocol/${p.slug}` : null),
    })),
    ...entityTags.map(e => ({
      kind: "mem", key: `em:${e.slug}`, name: e.name, display: e.name, type: e.type || "other",
      avatars: e.avatars || [], url: e.url || null,
    })),
  ];

  const byName = new Map();
  const byFirst = new Map();
  const out = [];
  for (const c of cand) {
    const dispLC = c.display.toLowerCase();
    if (byName.has(dispLC)) continue;
    const fw = dispLC.split(/\s+/)[0].replace(/[^a-z0-9]/g, "");
    const hasFw = fw.length >= 4;
    const prevIdx = hasFw ? byFirst.get(fw) : undefined;
    if (prevIdx === undefined) {
      out.push(c);
      const idx = out.length - 1;
      byName.set(dispLC, idx);
      if (hasFw) byFirst.set(fw, idx);
      continue;
    }
    const prev = out[prevIdx];
    const winner = pickBetter(prev, c);
    if (winner !== prev) {
      byName.delete(prev.display.toLowerCase());
      out[prevIdx] = winner;
      byName.set(winner.display.toLowerCase(), prevIdx);
    }
  }
  return out;
}

const BRIDGE_CAT = /canonical\s*bridge/i;

// Severity classification — drives the resting-state left-edge border colour.
const SEV_RED   = /\b(hack(?:ed|s)?|exploit(?:ed|s)?|breach(?:ed|es)?|attack(?:ed|s)?|vulnerabilit(?:y|ies)|drain(?:ed|s)?|stolen|steal|rug(?:s|ged|pull)?)\b/i;
const SEV_GOLD  = /\b(governance|proposals?|vot(?:e|es|ing)|dao|upgrade[ds]?|v[34])\b/i;
const SEV_GREEN = /\b(launch(?:ed|es)?|deploy(?:ed|s|ment)?|yield|apy|integrat(?:ion|ions|ed|e)|partnerships?)\b/i;

function classifySeverity(title, body, hack) {
  const text = `${title || ""} ${body || ""}`;
  if (hack || SEV_RED.test(text)) return "red";
  if (SEV_GOLD.test(text))        return "gold";
  if (SEV_GREEN.test(text))       return "green";
  return "neutral";
}

function sourceDotCount(n) {
  if (n >= 4) return 3;
  if (n >= 2) return 2;
  return 1;
}

// ── Importance score badge ─────────────────────────────────────────────────
function scoreTier(score) {
  if (score >= 80) return "hi";
  if (score >= 50) return "mid";
  if (score >= 20) return "lo";
  return "min";
}

function ScoreBadge({ score }) {
  if (score == null) return null;
  const clamped = Math.max(0, Math.min(100, Math.round(score)));
  return (
    <span
      className={`score-badge score-badge--${scoreTier(clamped)}`}
      title={`Importance ${clamped}/100`}
      aria-label={`Importance score ${clamped} of 100`}
    >
      <span className="score-badge-num">{clamped}</span>
      <span className="score-badge-bar" aria-hidden="true">
        <span className="score-badge-fill" style={{ width: `${clamped}%` }} />
      </span>
    </span>
  );
}

// ── Entity icon stack — lives on the title line ───────────────────────────
function InlineEntityIcons({ data, onTagSearch }) {
  const { protocols = [], chains = [], entityTags = [] } = data || {};
  const merged  = buildEntities(chains, protocols, entityTags);
  const visible = merged.slice(0, MAX_ENTITIES);
  const overflow = merged.length - MAX_ENTITIES;
  if (!visible.length) return null;
  return (
    <div className="bullet-inline-tags">
      {visible.map((item, i) => (
        <button
          key={item.key}
          className="entity-icon"
          type="button"
          style={{ zIndex: visible.length - i }}
          onClick={e => { e.stopPropagation(); onTagSearch?.(item.display); }}
          aria-label={`${item.display} — click to search`}
        >
          <EntityAvatar avatars={item.avatars} type={item.type} name={item.display} />
          <span className="entity-icon-name" aria-hidden="true">{item.display}</span>
        </button>
      ))}
      {overflow > 0 && (
        <span className="entity-icon-overflow">+{overflow}</span>
      )}
    </div>
  );
}

// ── Category chips — stays below the title row ────────────────────────────
function InlineCats({ data, onTagSearch }) {
  const { protocols = [] } = data || {};
  const cats = [...new Set(
    protocols.map(p => p.category).filter(Boolean).filter(c => !BRIDGE_CAT.test(c))
  )].slice(0, 2);
  if (!cats.length) return null;
  return (
    <div className="bullet-cats">
      {cats.map(cat => (
        <button
          key={cat}
          type="button"
          className="bullet-cat"
          onClick={e => { e.stopPropagation(); onTagSearch?.(cat); }}
          aria-label={`Search ${cat}`}
        >
          {cat}
        </button>
      ))}
    </div>
  );
}

// ── Bullet item ────────────────────────────────────────────────────────────
export default function BulletItem({ title, body, hack, ts, projectHint, importanceScore, sourceCount, selected, cursor, onSelect, onTagSearch }) {
  const severity = classifySeverity(title, body, hack);
  const ago = fmtAgo(ts);
  const showSources = sourceCount != null && sourceCount >= 2;
  return (
    <li
      className={`bullet${hack ? " hack" : ""}${selected ? " bullet--selected" : ""}${cursor ? " bullet--cursor" : ""}`}
      onClick={e => { e.stopPropagation(); onSelect?.(); }}
      role="button"
      tabIndex={0}
      onKeyDown={e => (e.key === "Enter" || e.key === " ") && onSelect?.()}
      aria-pressed={selected}
    >
      <span
        className={`bullet-importance bullet-sev--${severity}`}
        aria-hidden="true"
      />
      <div className="bullet-layout">
        <div className="bullet-main">
          <div className="bullet-title-row">
            <h3 className="bullet-title">{title || "Update"}</h3>
            <InlineEntityIcons data={projectHint} onTagSearch={onTagSearch} />
          </div>
          <InlineCats data={projectHint} onTagSearch={onTagSearch} />
          {body && <p className="bullet-text">{body}</p>}
        </div>
        <div className="bullet-aside">
          <ScoreBadge score={importanceScore} />
          {(ago || showSources) && (
            <div className="bullet-meta">
              {ago && (
                <time className="bullet-time" dateTime={ts} title={absTime(ts) || undefined}
                      suppressHydrationWarning>{ago}</time>
              )}
              {showSources && (
                <span className="bullet-sources">
                  <span className="bullet-source-dots" aria-hidden="true">
                    {"●".repeat(sourceDotCount(sourceCount))}
                  </span>
                  {sourceCount}<span className="bullet-sources-label"> sources</span>
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}
