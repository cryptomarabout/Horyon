"use client";

import { useState, useEffect } from "react";
import { stateMeta } from "../../lib/narratives";
import { TYPE_META, avatarCandidates } from "../../lib/entityGraph";
import { fmtTvl, fmtDayAgo, getDomain } from "../../lib/format";
import { monogram } from "./EntityTag";
import EmptyState from "./ui/EmptyState";
import PanelSection from "./ui/PanelSection";
import PanelHeader from "./ui/PanelHeader";
import PanelBody from "./ui/PanelBody";

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
      data-r="0"
      onLoad={(e) => {
        const mono = e.currentTarget.previousElementSibling;
        if (mono?.classList.contains("map-avatar-mono")) mono.style.visibility = "hidden";
      }}
      onError={(e) => {
        const el = e.currentTarget;
        const tries = Number(el.dataset.r);
        if (tries < 2) {
          el.dataset.r = String(tries + 1);
          const url = cands[Number(el.dataset.i)];
          setTimeout(() => { el.src = `${url}${url.includes("?") ? "&" : "?"}_r=${tries + 1}`; },
            500 * (tries + 1));
          return;
        }
        el.dataset.r = "0";
        const i = Number(el.dataset.i) + 1;
        if (i < cands.length) { el.dataset.i = String(i); el.src = cands[i]; }
        else { el.style.display = "none"; }
      }}
    />
  );
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

// Live "Latest mentions" for the selected entity.
function RecentMentions({ node }) {
  const [state, setState] = useState({ loading: true, items: [] });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, items: [] });
    fetch("/api/entity-mentions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: node.name, slug: node.slug }),
    })
      .then((r) => (r.ok ? r.json() : { mentions: [] }))
      .then((d) => { if (!cancelled) setState({ loading: false, items: d.mentions || [] }); })
      .catch(() => { if (!cancelled) setState({ loading: false, items: [] }); });
    return () => { cancelled = true; };
  }, [node.slug, node.name]);

  const { loading, items } = state;
  return (
    <PanelSection label="Latest Mentions" count={!loading && items.length ? items.length : null}>
      {loading ? (
        <p className="panel-ai-text" style={{ color: "var(--text-4)" }}>Loading recent coverage…</p>
      ) : items.length === 0 ? (
        <p className="panel-ai-text" style={{ color: "var(--text-4)" }}>No recent mentions in the feeds.</p>
      ) : (
        <EvidenceList items={items} />
      )}
    </PanelSection>
  );
}

// Shared co-citation / mention evidence list (`.eg-evidence` / `.eg-evi-row`),
// used by both the node "Latest Mentions" and the edge "Why they're linked".
function EvidenceList({ items }) {
  return (
    <div className="eg-evidence">
      {items.map((m, i) => {
        const dom = getDomain(m.link);
        const ago = fmtDayAgo(m.ts);
        const Tag = m.link ? "a" : "div";
        return (
          <Tag key={i} className="eg-evi-row"
            {...(m.link ? { href: m.link, target: "_blank", rel: "noreferrer" } : {})}>
            <span className="eg-evi-snippet">{m.snippet || m.link}</span>
            <span className="eg-evi-meta">
              {(m.source || dom) && <span>{m.source || dom}</span>}
              {ago && <span>· {ago}</span>}
            </span>
          </Tag>
        );
      })}
    </div>
  );
}

// Protocol fundamentals — the DeFiLlama metrics the map collects but the graph
// views never surface: net flows (7d/1d), valuation (Mcap/TVL), deployment footprint.
const fmtPct = (v) => (v == null ? null : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
const flowDir = (v) => (v > 0 ? "up" : v < 0 ? "dn" : "flat");

function ProtocolFundamentals({ node }) {
  const tvl = fmtTvl(node.tvl);
  if (!tvl) return null;
  const c7 = fmtPct(node.tvlChange7d);
  const c1 = fmtPct(node.tvlChange1d);
  const stats = [
    { k: "TVL", v: tvl },
    c7 != null && { k: "7d flow", v: c7, cls: `pl-flow-val ${flowDir(node.tvlChange7d)}` },
    c1 != null && { k: "1d", v: c1, cls: `pl-flow-val ${flowDir(node.tvlChange1d)}` },
    node.mcapTvl != null && node.mcapTvl > 0 && { k: "Mcap/TVL", v: node.mcapTvl.toFixed(2) },
    node.chains?.length && { k: "Chains", v: String(node.chains.length) },
    node.tokenSymbol && { k: "Token", v: node.tokenSymbol },
  ].filter(Boolean);
  return (
    <PanelSection label="Fundamentals">
      <div className="eg-funda">
        {stats.map((s) => (
          <div className="eg-funda-stat" key={s.k}>
            <span className="eg-funda-k">{s.k}</span>
            <span className={`eg-funda-v ${s.cls || ""}`}>{s.v}</span>
          </div>
        ))}
      </div>
      {node.chains?.length > 1 && (
        <p className="eg-funda-chains" title="Chains this protocol is deployed on">
          {node.chains.slice(0, 6).join(" · ")}{node.chains.length > 6 ? ` +${node.chains.length - 6}` : ""}
        </p>
      )}
    </PanelSection>
  );
}

function NodeView({ node, neighbors, onClose, onFocusEntity }) {
  const tvl = fmtTvl(node.tvl);
  const sm  = node.narrativeState ? stateMeta(node.narrativeState) : null;
  return (
    <>
      <PanelHeader
        onClose={onClose}
        below={
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
        }
      >
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
      </PanelHeader>

      <PanelBody>
        <ProtocolFundamentals node={node} />
        <RecentMentions node={node} />
        <PanelSection label="Most Connected" count={neighbors.length}>
          {neighbors.length === 0 ? (
            <p className="panel-ai-text" style={{ color: "var(--text-4)" }}>No links at this strength.</p>
          ) : (
            <div className="eg-neighbors">
              {neighbors.map((n) => (
                <NeighborChip key={n.slug} n={n} onPick={onFocusEntity} />
              ))}
            </div>
          )}
        </PanelSection>
      </PanelBody>
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
      <PanelHeader
        onClose={onClose}
        below={
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
        }
      >
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "4px" }}>
          <span className="pw-eyebrow">Co-citation Link</span>
          <h2 className="panel-title">{edge.a.name} <span className="map-edge-x">×</span> {edge.b.name}</h2>
        </div>
      </PanelHeader>
      <PanelBody>
        <div className="eg-endpoints">
          {[edge.a, edge.b].map((e) => (
            <button key={e.slug} type="button" className={`eg-neighbor eg-neighbor--${e.type}`}
              onClick={() => onFocusEntity(e.slug)}>
              <span className={`mapfilter-dot egdot--${e.type}`} aria-hidden />
              <span className="eg-neighbor-name">{e.name}</span>
            </button>
          ))}
        </div>

        <PanelSection label="Why they're linked">
          {examples.length === 0 ? (
            <p className="panel-ai-text" style={{ color: "var(--text-4)" }}>
              No sample headlines stored for this link.
            </p>
          ) : (
            <EvidenceList items={examples} />
          )}
        </PanelSection>
      </PanelBody>
    </>
  );
}

export default function EntityMapPanel({ selected, neighbors = [], onClose, onFocusEntity }) {
  let content;
  if (selected?.kind === "node") {
    content = <NodeView node={selected.node} neighbors={neighbors} onClose={onClose} onFocusEntity={onFocusEntity} />;
  } else if (selected?.kind === "edge") {
    content = <EdgeView edge={selected.edge} onClose={onClose} onFocusEntity={onFocusEntity} />;
  } else {
    content = <EmptyState glyph="◉">Select an entity or a link<br />to inspect its connections</EmptyState>;
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
