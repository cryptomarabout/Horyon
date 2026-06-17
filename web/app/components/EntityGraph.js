"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import {
  forceSimulation, forceLink, forceManyBody,
  forceCenter, forceCollide, forceX, forceY,
} from "d3-force";
import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity } from "d3-zoom";
import { drag as d3drag } from "d3-drag";
import NarrativeViewToggle from "./NarrativeViewToggle";
import EntityMapPanel from "./EntityMapPanel";
import { TYPE_META, TYPES, avatarCandidates } from "../../lib/entityGraph";

// Two lenses on the same edges:
//  · Affinity (NPMI) — how much MORE than chance two entities co-occur. Surfaces
//    real relationships; ubiquitous hubs (Bitcoin) recede. The default.
//  · Volume — raw co-mention count. Most-discussed-together; hubs dominate.
const METRICS = {
  affinity: { label: "Affinity", levels: [0.1, 0.25, 0.4, 0.55], names: ["All", "Med", "High", "Tight"],
              hint: "How much more than chance two entities are mentioned together" },
  volume:   { label: "Volume",   levels: [2, 3, 4, 6],          names: ["All", "3+", "4+", "Strong"],
              hint: "Raw number of articles mentioning both entities" },
};
const DEFAULT_METRIC = "affinity";
const DEFAULT_LEVEL = 1;

const R_MIN = 6, R_MAX = 30;

function radiusFor(mc, minMc, maxMc) {
  if (maxMc <= minMc) return (R_MIN + R_MAX) / 2;
  const t = (Math.sqrt(Math.max(mc, minMc)) - Math.sqrt(minMc)) /
            (Math.sqrt(maxMc) - Math.sqrt(minMc));
  return R_MIN + t * (R_MAX - R_MIN);
}

function monogram(name) {
  const w = (name || "?").trim().split(/\s+/);
  if (w.length >= 2 && w[0] && w[1]) return (w[0][0] + w[1][0]).toUpperCase();
  return (name || "?").slice(0, 2).toUpperCase();
}

const edgeKey = (s, t) => (s < t ? `${s}|${t}` : `${t}|${s}`);
const safeId = (s) => `egclip-${String(s).replace(/[^a-z0-9_-]/gi, "_")}`;

// ── Type filter chips (= the color legend) ──────────────────────────────────
function TypeChips({ active, counts, onToggle, onAll }) {
  const allOn = TYPES.every((t) => active[t] || (counts[t] || 0) === 0);
  return (
    <div className="srcfilter mapfilter" role="group" aria-label="Filter by type">
      <button
        type="button"
        className={`srcfilter-chip srcfilter-all${allOn ? " is-active" : ""}`}
        aria-pressed={allOn}
        onClick={onAll}
      >
        All
      </button>
      {TYPES.filter((t) => (counts[t] || 0) > 0).map((t) => (
        <button
          key={t}
          type="button"
          className={`srcfilter-chip mapfilter-chip egfilter-chip--${t}${active[t] ? " is-active" : ""}`}
          aria-pressed={active[t]}
          onClick={() => onToggle(t)}
        >
          <span className={`mapfilter-dot egdot--${t}`} aria-hidden />
          <span>{TYPE_META[t].label}</span>
          <span className="srcfilter-n">{counts[t]}</span>
        </button>
      ))}
    </div>
  );
}

// ── Main graph root — owns ALL interaction state ────────────────────────────
export default function EntityGraph({ nodes = [], edges = [] }) {
  const [active, setActive] = useState(() =>
    Object.fromEntries(TYPES.map((t) => [t, true])));
  const [metric, setMetric] = useState(DEFAULT_METRIC);
  const [level, setLevel] = useState(DEFAULT_LEVEL);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);   // {kind:'node'|'edge', ...}
  const [panelOpen, setPanelOpen] = useState(false);

  const containerRef = useRef(null);
  const svgRef = useRef(null);
  const simRef = useRef(null);
  const viewRef = useRef(null);

  // ── Derived data ─────────────────────────────────────────────────────────
  const { minMc, maxMc } = useMemo(() => {
    const mcs = nodes.map((n) => n.mentionCount || 0);
    return { minMc: Math.min(...mcs, 1), maxMc: Math.max(...mcs, 1) };
  }, [nodes]);

  const counts = useMemo(() => {
    const c = {};
    for (const t of TYPES) c[t] = 0;
    for (const n of nodes) c[n.type] = (c[n.type] || 0) + 1;
    return c;
  }, [nodes]);

  const nodeBySlug = useMemo(
    () => new Map(nodes.map((n) => [n.slug, n])), [nodes]);

  // adjacency with weights → neighbor lists (for focus + the panel)
  const neighbors = useMemo(() => {
    const m = new Map();
    const push = (a, b, w) => {
      let arr = m.get(a);
      if (!arr) { arr = []; m.set(a, arr); }
      arr.push({ slug: b, weight: w });
    };
    for (const e of edges) { push(e.source, e.target, e.weight); push(e.target, e.source, e.weight); }
    for (const arr of m.values()) arr.sort((x, y) => y.weight - x.weight);
    return m;
  }, [edges]);

  // ── Selection handlers (stable) ──────────────────────────────────────────
  const selectNode = useCallback((node) => {
    setSelected({ kind: "node", node });
    setPanelOpen(true);
  }, []);
  const selectEdge = useCallback((edge) => {
    setSelected({ kind: "edge", edge });
    setPanelOpen(true);
  }, []);
  const clearSelection = useCallback(() => { setSelected(null); setPanelOpen(false); }, []);
  const focusEntity = useCallback((slug) => {
    const n = nodeBySlug.get(slug);
    if (n) selectNode(n);
  }, [nodeBySlug, selectNode]);

  // ── Build simulation + SVG (once per data set) ───────────────────────────
  useEffect(() => {
    const svgEl = svgRef.current, containerEl = containerRef.current;
    if (!svgEl || !containerEl || nodes.length === 0) return;

    let w = containerEl.clientWidth || 900;
    let h = containerEl.clientHeight || 640;

    const simNodes = nodes.map((n) => ({ ...n, r: radiusFor(n.mentionCount, minMc, maxMc) }));
    const byId = new Map(simNodes.map((n) => [n.slug, n]));
    const simEdges = edges
      .filter((e) => byId.has(e.source) && byId.has(e.target))
      .map((e) => ({ ...e }));

    const svg = select(svgEl);
    svg.selectAll("*").remove();

    const bg = svg.append("rect").attr("class", "map-bg")
      .attr("width", w).attr("height", h).on("click", clearSelection);

    const zoomRoot = svg.append("g").attr("class", "map-zoom");
    const edgeG = zoomRoot.append("g").attr("class", "map-edges");
    const edgeHitG = zoomRoot.append("g").attr("class", "map-edges-hit");
    const nodeG = zoomRoot.append("g").attr("class", "map-nodes");

    const lineSel = edgeG.selectAll("line").data(simEdges).join("line")
      .attr("class", "map-edge");

    const hitSel = edgeHitG.selectAll("line").data(simEdges).join("line")
      .attr("class", "map-edge-hit")
      .on("click", (event, d) => {
        event.stopPropagation();
        selectEdge({ a: d.source, b: d.target, weight: d.weight, npmi: d.npmi, examples: d.examples });
      });

    const nodeSel = nodeG.selectAll("g").data(simNodes).join("g")
      .attr("class", (d) => `map-node ty-${d.type}${d.narrativeState ? " map-node--narr" : ""}`)
      .style("cursor", "pointer");

    nodeSel.append("title").text((d) =>
      `${d.name} · ${TYPE_META[d.type]?.label?.replace(/s$/, "") || d.type} · ${d.mentionCount} mentions · ${d.degree} links`);
    nodeSel.append("circle").attr("class", "map-node-circle").attr("r", (d) => d.r);

    nodeSel.append("text").attr("class", "map-node-mono")
      .attr("text-anchor", "middle").attr("dy", "0.34em")
      .style("font-size", (d) => `${Math.max(7, d.r * 0.62)}px`)
      .text((d) => monogram(d.name));

    // Avatar = real logo → Twitter pic via unavatar → (remove → monogram shows).
    nodeSel.filter((d) => avatarCandidates(d).length > 0).each(function (d) {
      const cands = avatarCandidates(d);
      const g = select(this), cid = safeId(d.slug);
      g.append("clipPath").attr("id", cid).append("circle").attr("r", d.r - 1.5);
      let idx = 0;
      const img = g.append("image").attr("class", "map-node-logo").attr("href", cands[0])
        .attr("x", -(d.r - 1.5)).attr("y", -(d.r - 1.5))
        .attr("width", (d.r - 1.5) * 2).attr("height", (d.r - 1.5) * 2)
        .attr("clip-path", `url(#${cid})`).attr("preserveAspectRatio", "xMidYMid slice");
      img.on("error", function () {
        idx += 1;
        if (idx < cands.length) select(this).attr("href", cands[idx]);
        else select(this).remove();
      });
    });

    // Only the biggest hubs keep a resting label — everything else reveals on
    // hover/focus/select (CSS). Avoids the unreadable label pile-up in the core.
    const labelSet = new Set(
      [...simNodes].sort((a, b) => (b.mentionCount || 0) - (a.mentionCount || 0))
        .slice(0, 16).map((n) => n.slug)
    );
    nodeSel.append("text").attr("class", "map-node-label")
      .attr("text-anchor", "middle").attr("dy", (d) => d.r + 11)
      .style("opacity", (d) => (labelSet.has(d.slug) ? 1 : 0))
      .text((d) => d.name);

    // Strong repulsion (scaled by node size) + weak centering spreads the network
    // across the canvas instead of collapsing the hubs into a central hairball.
    const sim = forceSimulation(simNodes)
      .force("link", forceLink(simEdges).id((d) => d.slug)
        .distance((d) => 46 + d.source.r + d.target.r)
        .strength((d) => Math.min(0.04 + d.weight * 0.009, 0.4)))
      .force("charge", forceManyBody().strength((d) => -120 - d.r * 16).distanceMax(900))
      .force("collide", forceCollide().radius((d) => d.r + 7).iterations(3))
      .force("x", forceX(w / 2).strength(0.018))
      .force("y", forceY(h / 2).strength(0.018))
      .force("center", forceCenter(w / 2, h / 2));

    const renderPositions = () => {
      lineSel.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      hitSel.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);
    };

    // Zoom / pan. Track whether the user has taken control so auto-fit backs off.
    let userZoomed = false;
    const zoomB = d3zoom().scaleExtent([0.12, 5])
      .on("zoom", (event) => {
        zoomRoot.attr("transform", event.transform);
        if (event.sourceEvent) userZoomed = true;
      });
    svg.call(zoomB).on("dblclick.zoom", null);

    // Frame the whole graph in the viewport (visible nodes only, with padding).
    const fitView = (force = false) => {
      if ((userZoomed && !force) || !simNodes.length) return;
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      nodeSel.each(function (n) {
        if (this.style.display === "none") return;
        minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
        minY = Math.min(minY, n.y - n.r); maxY = Math.max(maxY, n.y + n.r);
      });
      if (!isFinite(minX)) return;
      const cw = containerEl.clientWidth || w, ch = containerEl.clientHeight || h;
      const gw = maxX - minX || 1, gh = maxY - minY || 1, pad = 70;
      const scale = Math.max(0.12, Math.min((cw - pad) / gw, (ch - pad) / gh, 1.6));
      const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
      const t = zoomIdentity.translate(cw / 2, ch / 2).scale(scale).translate(-cx, -cy);
      svg.call(zoomB.transform, t);
    };

    const reduced = typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced) { sim.stop(); sim.tick(400); renderPositions(); fitView(); }
    else {
      sim.alpha(1).on("tick", renderPositions);
      sim.on("end", () => fitView());
      // Fallback frame once the layout has roughly settled (in case "end" is slow).
      setTimeout(() => fitView(), 1800);
    }

    const nodeDrag = d3drag()
      .on("start", (event, d) => {
        event.sourceEvent?.stopPropagation?.();
        if (!event.active) sim.alphaTarget(0.2).restart();
        d.fx = d.x; d.fy = d.y; d.__sx = event.x; d.__sy = event.y; d.__moved = false;
      })
      .on("drag", (event, d) => {
        d.fx = event.x; d.fy = event.y;
        if (Math.hypot(event.x - d.__sx, event.y - d.__sy) > 4) d.__moved = true;
      })
      .on("end", (event, d) => {
        if (!event.active) sim.alphaTarget(0);
        d.fx = null; d.fy = null;
        if (!d.__moved) selectNode(d);
      });
    nodeSel.call(nodeDrag);

    simRef.current = sim;
    viewRef.current = { svg, zoomB, bg, nodeSel, lineSel, hitSel, simNodes, fit: () => fitView(true) };

    const ro = new ResizeObserver(() => {
      const nw = containerEl.clientWidth, nh = containerEl.clientHeight;
      if (!nw || !nh || (nw === w && nh === h)) return;
      w = nw; h = nh;
      bg.attr("width", w).attr("height", h);
      sim.force("center", forceCenter(w / 2, h / 2));
      sim.force("x").x(w / 2); sim.force("y").y(h / 2);
      if (!reduced) sim.alpha(0.2).restart(); else { sim.tick(60); renderPositions(); }
    });
    ro.observe(containerEl);

    return () => {
      ro.disconnect(); sim.stop();
      svg.on(".zoom", null); svg.selectAll("*").remove();
      simRef.current = null; viewRef.current = null;
    };
  }, [nodes, edges, minMc, maxMc, clearSelection, selectNode, selectEdge]);

  // ── Apply weight / type / search / selection (no physics restart) ────────
  useEffect(() => {
    const v = viewRef.current;
    if (!v) return;
    const { nodeSel, lineSel, hitSel } = v;
    const q = query.trim().toLowerCase();

    const selSlug = selected?.kind === "node" ? selected.node.slug : null;
    const selEdgeK = selected?.kind === "edge"
      ? edgeKey(selected.edge.a.slug, selected.edge.b.slug) : null;

    // focus set: a node's neighborhood, an edge's endpoints, or the search matches
    let focus = null;
    if (selSlug) focus = new Set([selSlug, ...((neighbors.get(selSlug) || []).map((n) => n.slug))]);
    else if (selEdgeK) focus = new Set([selected.edge.a.slug, selected.edge.b.slug]);
    else if (q) focus = new Set(nodes
      .filter((n) => n.name.toLowerCase().includes(q) || n.slug.includes(q))
      .map((n) => n.slug));

    const isAff = metric === "affinity";
    const thr = METRICS[metric].levels[level];
    const passes = (d) => (isAff ? (d.npmi ?? 0) >= thr : d.weight >= thr);

    const typeOn = (n) => !!active[n.type];
    const edgeShown = (d) =>
      passes(d) && typeOn(d.source) && typeOn(d.target) &&
      (!focus || (focus.has(d.source.slug) && focus.has(d.target.slug)) ||
        (selSlug && (d.source.slug === selSlug || d.target.slug === selSlug)));

    // A node is shown if its type is on AND it keeps at least one visible edge
    // (prunes the dust that appears as the threshold rises) — unless it's the
    // focus/selection itself.
    const visibleDeg = new Map();
    for (const d of (v.simNodes || [])) visibleDeg.set(d.slug, 0);
    lineSel.each((d) => {
      if (passes(d) && typeOn(d.source) && typeOn(d.target)) {
        visibleDeg.set(d.source.slug, (visibleDeg.get(d.source.slug) || 0) + 1);
        visibleDeg.set(d.target.slug, (visibleDeg.get(d.target.slug) || 0) + 1);
      }
    });

    const nodeShown = (d) =>
      typeOn(d) && ((visibleDeg.get(d.slug) || 0) > 0 || d.slug === selSlug ||
        (focus && focus.has(d.slug)));

    nodeSel
      .style("display", (d) => (nodeShown(d) ? null : "none"))
      .style("opacity", (d) => (focus && !focus.has(d.slug) ? 0.12 : 1))
      .classed("is-selected", (d) => d.slug === selSlug)
      .classed("is-focus", (d) => !!focus && focus.has(d.slug) && d.slug !== selSlug);

    lineSel
      .style("display", (d) => (edgeShown(d) ? null : "none"))
      .style("opacity", (d) => (focus && !(focus.has(d.source.slug) && focus.has(d.target.slug)) ? 0.06 : null))
      .attr("stroke-width", (d) => (isAff
        ? 0.5 + Math.max(0, d.npmi ?? 0) * 3.2
        : Math.min(0.6 + Math.log2(d.weight) * 0.7, 4)))
      .classed("is-active", (d) => edgeKey(d.source.slug, d.target.slug) === selEdgeK);
    hitSel.style("display", (d) => (edgeShown(d) ? null : "none"));
  }, [active, metric, level, query, selected, nodes, neighbors]);

  // Re-frame when the lens (metric) changes — the visible set shifts a lot.
  useEffect(() => {
    const id = setTimeout(() => viewRef.current?.fit?.(), 140);
    return () => clearTimeout(id);
  }, [metric]);

  // ── URL state: ?node=<slug> ──────────────────────────────────────────────
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (selected?.kind === "node") url.searchParams.set("node", selected.node.slug);
    else url.searchParams.delete("node");
    window.history.replaceState(null, "", url);
  }, [selected]);

  useEffect(() => {
    if (typeof window === "undefined" || !nodes.length) return;
    const slug = new URLSearchParams(window.location.search).get("node");
    const n = slug && nodeBySlug.get(slug);
    if (n) { setSelected({ kind: "node", node: n }); setPanelOpen(true); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes]);

  // ── Toolbar handlers ─────────────────────────────────────────────────────
  const toggleType = useCallback((t) => setActive((a) => ({ ...a, [t]: !a[t] })), []);
  const allTypes = useCallback(() =>
    setActive(Object.fromEntries(TYPES.map((t) => [t, true]))), []);
  const recenter = useCallback(() => {
    viewRef.current?.fit?.();
  }, []);

  const panelNeighbors = useMemo(() => {
    if (selected?.kind !== "node") return [];
    return (neighbors.get(selected.node.slug) || [])
      .slice(0, 12)
      .map((n) => ({ ...nodeBySlug.get(n.slug), weight: n.weight }))
      .filter((n) => n.slug);
  }, [selected, neighbors, nodeBySlug]);

  const empty = nodes.length === 0;

  return (
    <div className="feed-grid">
      <div className="feed-left map-left">
        <div className="view-head view-head--row map-head">
          <div className="view-head-titles">
            <h1 className="view-title">Entity Map</h1>
            <p className="view-sub">
              {empty ? "No graph yet" : <>
                {nodes.length} entities · {edges.length} links ·{" "}
                {metric === "affinity" ? "linked by affinity (co-mention vs chance)" : "linked by co-mention volume"}
              </>}
            </p>
          </div>
          <NarrativeViewToggle />
        </div>

        {!empty && (
          <div className="map-toolbar">
            <TypeChips active={active} counts={counts} onToggle={toggleType} onAll={allTypes} />
            <div className="map-toolbar-right">
              <div className="eg-metric" role="group" aria-label="Edge metric">
                {Object.entries(METRICS).map(([k, m]) => (
                  <button
                    key={k}
                    type="button"
                    className={`eg-metric-btn${metric === k ? " is-active" : ""}`}
                    aria-pressed={metric === k}
                    title={m.hint}
                    onClick={() => setMetric(k)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <div className="eg-weight" role="group" aria-label="Link strength">
                {METRICS[metric].names.map((nm, i) => (
                  <button
                    key={i}
                    type="button"
                    className={`eg-weight-btn${level === i ? " is-active" : ""}`}
                    aria-pressed={level === i}
                    onClick={() => setLevel(i)}
                  >
                    {nm}
                  </button>
                ))}
              </div>
              <div className="map-search">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor"
                  strokeWidth="1.8" strokeLinecap="round" aria-hidden>
                  <circle cx="6.5" cy="6.5" r="4.5" /><line x1="10.5" y1="10.5" x2="14" y2="14" />
                </svg>
                <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
                  placeholder="Find entity…" aria-label="Search entities" spellCheck={false} />
                {query && <button className="map-search-clear" onClick={() => setQuery("")} aria-label="Clear">✕</button>}
              </div>
              <button type="button" className="map-recenter" onClick={recenter} title="Reset view">Recenter</button>
            </div>
          </div>
        )}

        <div className="map-canvas" ref={containerRef}>
          {empty ? (
            <div className="feed-empty">
              <div className="feed-empty-glyph" aria-hidden>◈</div>
              <p>No entity graph yet.<br />The co-occurrence job builds it from recent feeds.</p>
            </div>
          ) : (
            <svg ref={svgRef} className="map-svg" role="img"
              aria-label="Force-directed map of crypto entities linked by co-mention" />
          )}
          {!empty && <div className="map-hint" aria-hidden>scroll to zoom · drag to pan</div>}
        </div>
      </div>

      <div className={`feed-right${panelOpen ? " panel-open" : ""}`}>
        <EntityMapPanel
          selected={selected}
          neighbors={panelNeighbors}
          onClose={clearSelection}
          onFocusEntity={focusEntity}
        />
      </div>
    </div>
  );
}
