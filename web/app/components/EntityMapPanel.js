"use client";

import { stateMeta } from "../../lib/narratives";
import { TYPE_META, avatarCandidates } from "../../lib/entityGraph";

// Avatar with a stateless fallback cascade: real logo → Twitter pic (unavatar) →
// monogram. Walks the candidate list via a data-attr, no React state.
function AvatarImg({ node, className }) {
  const cands = avatarCandidates(node);
  if (!cands.length) return null;
  return (
    <img
      className={className}
      src={cands[0]}
      alt=""
      data-i="0"
      onError={(e) => {
        const el = e.currentTarget;
        const i = Number(el.dataset.i) + 1;
        if (i < cands.length) { el.dataset.i = String(i); el.src = cands[i]; }
        else { el.style.display = "none"; }
      }}
    />
  );
}

function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return null; }
}
const MO = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtAgo(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (isNaN(t)) return null;
  const days = Math.floor((Date.now() - t) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  const d = new Date(t);
  return `${MO[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

// ── Detail drawer for the entity map ────────────────────────────────────────
// Pure presentation — all state lives in EntityGraph. Mirrors the RightPanel
// visual system (panel-header / panel-scroll / panel-body / chips).

function monogram(name) {
  const w = (name || "?").trim().split(/\s+/);
  if (w.length >= 2 && w[0] && w[1]) return (w[0][0] + w[1][0]).toUpperCase();
  return (name || "?").slice(0, 2).toUpperCase();
}

function fmtTvl(usd) {
  if (usd == null) return null;
  if (usd >= 1e12) return `$${(usd / 1e12).toFixed(2)}T`;
  if (usd >= 1e9)  return `$${(usd / 1e9).toFixed(1)}B`;
  if (usd >= 1e6)  return `$${(usd / 1e6).toFixed(0)}M`;
  if (usd > 0)     return `$${usd.toLocaleString()}`;
  return null;
}

function Avatar({ node, big = false }) {
  return (
    <span className={`map-avatar eg-avatar--${node.type}${big ? " map-avatar--lg" : ""}`}>
      <span className="map-avatar-mono" aria-hidden>{monogram(node.name)}</span>
      <AvatarImg node={node} className="map-avatar-img" />
    </span>
  );
}

function NeighborChip({ n, onPick }) {
  return (
    <button type="button" className={`eg-neighbor eg-neighbor--${n.type}`} onClick={() => onPick(n.slug)}>
      <span className={`mapfilter-dot egdot--${n.type}`} aria-hidden />
      <span className="eg-neighbor-name">{n.name}</span>
      <span className="eg-neighbor-w" title={`${n.weight} co-citations`}>{n.weight}</span>
    </button>
  );
}

function NodeView({ node, neighbors, onClose, onFocusEntity }) {
  const tvl = fmtTvl(node.tvl);
  const sm = node.narrativeState ? stateMeta(node.narrativeState) : null;
  return (
    <>
      <div className="panel-header">
        <div className="panel-title-row">
          <div style={{ display: "flex", alignItems: "center", gap: "11px", flex: 1, minWidth: 0 }}>
            <Avatar node={node} big />
            <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: "4px" }}>
              <h2 className="panel-title" style={{ WebkitLineClamp: 2 }}>{node.name}</h2>
              <div className="eg-badge-row">
                <span className={`map-type-badge eg-badge--${node.type}`}>
                  {TYPE_META[node.type]?.label?.replace(/s$/, "") || "Entity"}
                </span>
                {node.category && <span className="eg-cat">{node.category}</span>}
              </div>
            </div>
          </div>
          <button className="panel-close" onClick={onClose} aria-label="Close panel">✕</button>
        </div>
        <div className="map-meta-row">
          <span className="map-meta-stat"><span className="map-meta-num">{node.mentionCount}</span> mentions</span>
          <span className="map-meta-stat"><span className="map-meta-num">{node.degree}</span> links</span>
          {tvl && <span className="map-meta-stat"><span className="map-meta-num">{tvl}</span> TVL</span>}
          {sm && (
            <span className={`eg-narr-pill eg-narr-pill--${sm.cls}`}>
              <span aria-hidden>{sm.glyph}</span> {sm.label}
            </span>
          )}
          {node.twitterHandle && (
            <a className="panel-src-link" target="_blank" rel="noreferrer"
              href={`https://x.com/${node.twitterHandle.replace(/^@/, "")}`}>
              @{node.twitterHandle.replace(/^@/, "")}
            </a>
          )}
        </div>
      </div>

      <div className="panel-scroll">
        <div className="panel-body">
          {node.summary && (
            <div>
              <div className="panel-section-label">Analyst Memory</div>
              <p className="panel-ai-text">{node.summary}</p>
            </div>
          )}
          <div>
            <div className="panel-section-label">
              Most Connected · {neighbors.length}
            </div>
            {neighbors.length === 0 ? (
              <p className="panel-ai-text" style={{ color: "var(--text-4)" }}>No links at this strength.</p>
            ) : (
              <div className="eg-neighbors">
                {neighbors.map((n) => (
                  <NeighborChip key={n.slug} n={n} onPick={onFocusEntity} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function affinityLabel(npmi) {
  if (npmi == null) return null;
  if (npmi >= 0.5) return "very high";
  if (npmi >= 0.35) return "high";
  if (npmi >= 0.2) return "notable";
  if (npmi >= 0.05) return "weak";
  return "incidental";
}

function EdgeView({ edge, onClose, onFocusEntity }) {
  const examples = Array.isArray(edge.examples) ? edge.examples : [];
  const aff = affinityLabel(edge.npmi);
  return (
    <>
      <div className="panel-header">
        <div className="panel-title-row">
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "4px" }}>
            <span className="pw-eyebrow">Co-citation Link</span>
            <h2 className="panel-title">{edge.a.name} <span className="map-edge-x">×</span> {edge.b.name}</h2>
          </div>
          <button className="panel-close" onClick={onClose} aria-label="Close panel">✕</button>
        </div>
        <div className="map-meta-row">
          <span className="map-meta-stat">
            <span className="map-meta-num">{edge.weight}</span> co-mentions
          </span>
          {edge.npmi != null && (
            <span className="eg-aff-pill" title="NPMI — how much more than chance these co-occur">
              affinity {edge.npmi.toFixed(2)}{aff ? ` · ${aff}` : ""}
            </span>
          )}
        </div>
      </div>
      <div className="panel-scroll">
        <div className="panel-body">
          <div className="eg-endpoints">
            {[edge.a, edge.b].map((e) => (
              <button key={e.slug} type="button" className={`eg-neighbor eg-neighbor--${e.type}`}
                onClick={() => onFocusEntity(e.slug)}>
                <span className={`mapfilter-dot egdot--${e.type}`} aria-hidden />
                <span className="eg-neighbor-name">{e.name}</span>
              </button>
            ))}
          </div>

          <div>
            <div className="panel-section-label">Why they're linked</div>
            {examples.length === 0 ? (
              <p className="panel-ai-text" style={{ color: "var(--text-4)" }}>
                No sample headlines stored for this link.
              </p>
            ) : (
              <div className="eg-evidence">
                {examples.map((ex, i) => {
                  const dom = domainOf(ex.link);
                  const ago = fmtAgo(ex.ts);
                  return (
                    <a key={i} className="eg-evi-row" href={ex.link} target="_blank" rel="noreferrer">
                      <span className="eg-evi-snippet">{ex.snippet || ex.link}</span>
                      <span className="eg-evi-meta">
                        {dom && <span>{dom}</span>}
                        {ago && <span>· {ago}</span>}
                      </span>
                    </a>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function EmptyState() {
  return (
    <div className="panel-empty">
      <div className="panel-empty-glyph">◉</div>
      <p className="panel-empty-label">
        Select an entity or a link<br />to inspect its connections
      </p>
    </div>
  );
}

export default function EntityMapPanel({ selected, neighbors = [], onClose, onFocusEntity }) {
  let content;
  if (selected?.kind === "node") {
    content = <NodeView node={selected.node} neighbors={neighbors} onClose={onClose} onFocusEntity={onFocusEntity} />;
  } else if (selected?.kind === "edge") {
    content = <EdgeView edge={selected.edge} onClose={onClose} onFocusEntity={onFocusEntity} />;
  } else {
    content = <EmptyState />;
  }

  const animKey = selected?.kind === "node"
    ? `n:${selected.node.slug}`
    : selected?.kind === "edge"
      ? `e:${selected.edge.a.slug}|${selected.edge.b.slug}`
      : "empty";

  return (
    <div className="panel-container">
      <div key={animKey} className="panel-anim">{content}</div>
    </div>
  );
}
