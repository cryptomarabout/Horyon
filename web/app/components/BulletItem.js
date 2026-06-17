"use client";

// ── Chevron icon ───────────────────────────────────────────────────────────
function ChevronIcon() {
  return (
    <svg width="9" height="9" viewBox="0 0 12 12" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="4,2 8,6 4,10" />
    </svg>
  );
}

function ExtLinkIcon() {
  return (
    <svg width="8" height="8" viewBox="0 0 10 10" fill="none"
      stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M4 2H2a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1V6" />
      <polyline points="6,1 9,1 9,4" />
      <line x1="9" y1="1" x2="5" y2="5" />
    </svg>
  );
}

// ── Relative time ───────────────────────────────────────────────────────────
// Source article publish time → discreet "x ago" label (relative to now).
function timeAgo(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const diffMin = Math.floor((Date.now() - t) / 60000);
  if (diffMin < 1)  return "now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const h = Math.floor(diffMin / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

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
const MAX_ENTITIES = 3;

function baseName(name) {
  return name
    .replace(/\s+v?\d+(\.\d+)*$/i, "")
    .replace(/\s*\(v?\d+(\.\d+)*\)$/i, "")
    .trim();
}

// Deduplicate by first word: shorter names win
// Merge chains + DeFiLlama protocols + entity_memory tags into ONE ranked list,
// deduped by exact display name and by first word (≥4 chars) with the SHORTEST
// display winning. Shortest-wins collapses sub-product / piggyback names onto the
// clean parent ACROSS sources: a DeFiLlama "Morpho Blue"/"Kraken Bitcoin"/"Coinbase
// Bridge" folds onto the entity_memory "Morpho"/"Kraken"/"Coinbase"; "Aave V3" + "Aave"
// → "Aave". Without this the ugly DeFiLlama sub-product wins the slot (MAX_ENTITIES=3)
// and crowds out the real name.
const KIND_RANK = { chain: 0, proto: 1, mem: 2 };

function pickBetter(a, b) {
  if (a.display.length !== b.display.length) return a.display.length < b.display.length ? a : b;
  if (!!a.logo !== !!b.logo) return a.logo ? a : b;
  return KIND_RANK[a.kind] <= KIND_RANK[b.kind] ? a : b;
}

function buildEntities(chains, protocols, entityTags = []) {
  const cand = [
    ...chains.map(c => ({
      kind: "chain", key: `c:${c.name}`, name: c.name, display: c.name,
      logo: `https://icons.llamao.fi/icons/chains/rsz_${c.name.toLowerCase()}.jpg`,
      url:  c.url || null,
    })),
    ...protocols.map(p => ({
      kind: "proto", key: `p:${p.name}`, name: p.name, display: baseName(p.name),
      logo: p.logo_url || null,
      url:  p.url || (p.slug ? `https://defillama.com/protocol/${p.slug}` : null),
    })),
    ...entityTags.map(e => ({
      kind: "mem", key: `em:${e.slug}`, name: e.name, display: e.name,
      logo: e.logo || null, url: e.url || null, isMemoryOnly: e.isMemoryOnly,
    })),
  ];

  const byName = new Map();   // display(lower) → out index
  const byFirst = new Map();  // first word     → out index
  const out = [];
  for (const c of cand) {
    const dispLC = c.display.toLowerCase();
    if (byName.has(dispLC)) continue;
    const fw = dispLC.split(/\s+/)[0];
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
// Priority: red (security) > gold (governance) > green (growth) > neutral.
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

// Source-count → number of signal dots (1–3). Only rendered when sourceCount ≥ 2.
function sourceDotCount(n) {
  if (n >= 4) return 3;
  if (n >= 2) return 2;
  return 1;
}

// ── Importance score ring — 0–100 with the number centred ──────────────────
const RING_R = 13;
const RING_C = 2 * Math.PI * RING_R;

function scoreTier(score) {
  if (score >= 80) return "hi";
  if (score >= 50) return "mid";
  if (score >= 20) return "lo";
  return "min";
}

function ScoreRing({ score }) {
  if (score == null) return null;
  const clamped = Math.max(0, Math.min(100, Math.round(score)));
  const offset = RING_C * (1 - clamped / 100);
  return (
    <span
      className={`score-ring score-ring--${scoreTier(clamped)}`}
      title={`Importance ${clamped}/100`}
      aria-label={`Importance score ${clamped} of 100`}
    >
      <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true">
        <circle className="score-ring-track" cx="15" cy="15" r={RING_R} />
        <circle
          className="score-ring-prog"
          cx="15" cy="15" r={RING_R}
          strokeDasharray={RING_C}
          strokeDashoffset={offset}
          transform="rotate(-90 15 15)"
        />
      </svg>
      <span className="score-ring-num">{clamped}</span>
    </span>
  );
}

// ── Inline tags — entity names + category chips, visible at all sizes ─────
function InlineTags({ data, onTagSearch, sourceCount, timeNode }) {
  const { protocols = [], chains = [], entityTags = [] } = data || {};
  // One unified, first-word-deduped list across chains + DeFiLlama + entity_memory.
  const merged   = buildEntities(chains, protocols, entityTags);
  const visible  = merged.slice(0, MAX_ENTITIES);
  const overflow = merged.length - MAX_ENTITIES;
  const cats = [...new Set(
    protocols.map(p => p.category).filter(Boolean).filter(c => !BRIDGE_CAT.test(c))
  )];

  const showSources = sourceCount != null && sourceCount >= 2;
  if (!visible.length && !cats.length && !showSources && !timeNode) return null;

  return (
    <div className="bullet-inline-tags">
      {visible.map(item => {
        const cls = item.kind === "chain"
          ? " entity-tag--chain"
          : item.kind === "mem"
            ? ` entity-tag--memory${item.isMemoryOnly ? " entity-tag--no-logo" : ""}`
            : "";
        return (
          <span
            key={item.key}
            className={`entity-tag${cls}`}
            role="button"
            tabIndex={0}
            title={item.name}
            onClick={e => { e.stopPropagation(); onTagSearch?.(item.display); }}
            onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.stopPropagation(), onTagSearch?.(item.display))}
            aria-label={`Search ${item.display}`}
          >
            {item.logo && (
              <img src={item.logo} alt="" className="entity-logo"
                onError={e => { e.currentTarget.style.display = "none"; }} />
            )}
            <span className="entity-name">{item.display}</span>
            {item.url && (
              <a href={item.url} target="_blank" rel="noopener noreferrer"
                 className="entity-tag-ext"
                 onClick={e => e.stopPropagation()}
                 aria-label={`Open ${item.name}`}>
                <ExtLinkIcon />
              </a>
            )}
          </span>
        );
      })}
      {overflow > 0 && <span className="entity-overflow">+{overflow}</span>}
      {showSources && (
        <span className="bullet-sources">
          <span className="bullet-source-dots" aria-hidden="true">
            {"●".repeat(sourceDotCount(sourceCount))}
          </span>
          {sourceCount} sources
        </span>
      )}
      {cats.map(cat => (
        <span
          key={cat}
          className="bullet-cat"
          role="button"
          tabIndex={0}
          onClick={e => { e.stopPropagation(); onTagSearch?.(cat); }}
          onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.stopPropagation(), onTagSearch?.(cat))}
          aria-label={`Search ${cat}`}
        >
          {cat}
        </span>
      ))}
      {timeNode}
    </div>
  );
}

// ── Bullet item ────────────────────────────────────────────────────────────
export default function BulletItem({ title, body, hack, ts, projectHint, importanceScore, sourceCount, selected, cursor, onSelect, onTagSearch }) {
  const severity = classifySeverity(title, body, hack);
  const ago = timeAgo(ts);
  const timeNode = ago ? (
    <time className="bullet-time" dateTime={ts} title={absTime(ts) || undefined}
          suppressHydrationWarning>{ago}</time>
  ) : null;
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
          <h3 className="bullet-title">{title || "Update"}</h3>
          {body && <p className="bullet-text">{body}</p>}
          <InlineTags data={projectHint} onTagSearch={onTagSearch} sourceCount={sourceCount} timeNode={timeNode} />
        </div>
        <div className="bullet-aside">
          <ScoreRing score={importanceScore} />
          <span className="bullet-chevron" aria-hidden="true"><ChevronIcon /></span>
        </div>
      </div>
    </li>
  );
}
