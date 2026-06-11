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
function buildEntities(chains, protocols) {
  const raw = [
    ...chains.map(c => ({
      key:     `c:${c.name}`,
      name:    c.name,
      display: c.name,
      logo:    `https://icons.llamao.fi/icons/chains/rsz_${c.name.toLowerCase()}.jpg`,
      url:     c.url || null,
      isChain: true,
    })),
    ...protocols.map(p => ({
      key:     `p:${p.name}`,
      name:    p.name,
      display: baseName(p.name),
      logo:    p.logo_url || null,
      url:     p.url || (p.slug ? `https://defillama.com/protocol/${p.slug}` : null),
      isChain: false,
    })),
  ];

  raw.sort((a, b) => a.display.length - b.display.length);

  const seenFirstWord = new Set();
  const out = [];
  for (const item of raw) {
    const fw = item.display.split(/\s+/)[0].toLowerCase();
    if (!seenFirstWord.has(fw)) {
      seenFirstWord.add(fw);
      out.push(item);
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
  const all      = buildEntities(chains, protocols);
  const visible  = all.slice(0, MAX_ENTITIES);
  const overflow = all.length - MAX_ENTITIES;
  const cats = [...new Set(
    protocols.map(p => p.category).filter(Boolean).filter(c => !BRIDGE_CAT.test(c))
  )];

  // Deduplicate entity_memory tags against DeFiLlama entries:
  // - exact name match ("Aave" already shown → skip "Aave")
  // - first-word match ("Aave" shown → skip "Aave Labs", "Aave V3", etc.)
  //   only apply when the first word is ≥ 4 chars to avoid over-filtering on short words
  const shownNames = new Set(visible.map(e => e.display.toLowerCase()));
  const shownFirstWords = new Set(
    visible
      .map(e => e.display.toLowerCase().split(/\s+/)[0])
      .filter(w => w.length >= 4)
  );
  const extraEntities = entityTags.filter(e => {
    const nameLC = e.name.toLowerCase();
    const fw = nameLC.split(/\s+/)[0];
    return !shownNames.has(nameLC) && !shownFirstWords.has(fw);
  });
  // cap total tags at MAX_ENTITIES across both sources
  const remainingSlots = Math.max(0, MAX_ENTITIES - visible.length);
  const visibleExtra = extraEntities.slice(0, remainingSlots);

  const showSources = sourceCount != null && sourceCount >= 2;
  if (!visible.length && !visibleExtra.length && !cats.length && !showSources && !timeNode) return null;

  return (
    <div className="bullet-inline-tags">
      {visible.map(item => (
        <span
          key={item.key}
          className={`entity-tag${item.isChain ? " entity-tag--chain" : ""}`}
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
      ))}
      {visibleExtra.map(e => (
        <span
          key={`em:${e.slug}`}
          className={`entity-tag entity-tag--memory${e.isMemoryOnly ? " entity-tag--no-logo" : ""}`}
          role="button"
          tabIndex={0}
          title={e.name}
          onClick={ev => { ev.stopPropagation(); onTagSearch?.(e.name); }}
          onKeyDown={ev => (ev.key === "Enter" || ev.key === " ") && (ev.stopPropagation(), onTagSearch?.(e.name))}
          aria-label={`Search ${e.name}`}
        >
          {e.logo && (
            <img src={e.logo} alt="" className="entity-logo"
              onError={ev => { ev.currentTarget.style.display = "none"; }} />
          )}
          <span className="entity-name">{e.name}</span>
          {e.url && (
            <a href={e.url} target="_blank" rel="noopener noreferrer"
               className="entity-tag-ext"
               onClick={ev => ev.stopPropagation()}
               aria-label={`Open ${e.name}`}>
              <ExtLinkIcon />
            </a>
          )}
        </span>
      ))}
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
