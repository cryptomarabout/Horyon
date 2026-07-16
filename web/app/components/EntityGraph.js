"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import EntityMapPanel from "./EntityMapPanel";
import SearchPanel from "./panels/SearchPanel";
import MapToolbar from "./entityGraph/MapToolbar";
import EntityLeague from "./entityGraph/EntityLeague";
import IndexInfo from "./entityGraph/IndexInfo";
import { renderBoard } from "./entityGraph/renderBoard";
import { renderNetwork } from "./entityGraph/renderNetwork";
import {
  TYPES, METRICS, DEFAULT_METRIC, DEFAULT_LEVEL, DEFAULT_VIEW, edgeKey,
} from "../../lib/entityGraph";
import useHeaderSearch from "../../lib/useHeaderSearch";
import useMobilePanelBack from "../../lib/useMobilePanelBack";
import EmptyState from "./ui/EmptyState";

// ── Main graph root — owns ALL interaction state, switches Board ⇄ Network ────
// The two imperative D3 renderers live in ./entityGraph/{renderBoard,renderNetwork};
// this component holds React state, derives the shared data, drives the toolbar,
// and runs the threshold/type/search/selection pass over whichever view is mounted.
export default function EntityGraph({ nodes = [], edges = [], league = [] }) {
  const [view, setView] = useState(DEFAULT_VIEW);     // 'board' | 'network'
  const [active, setActive] = useState(() =>
    Object.fromEntries(TYPES.map((t) => [t, true])));
  const [metric, setMetric] = useState(DEFAULT_METRIC);
  const [level, setLevel] = useState(DEFAULT_LEVEL);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);   // {kind:'node'|'edge', ...}
  const [panelOpen, setPanelOpen] = useState(false);

  const containerRef = useRef(null);
  const svgRef = useRef(null);
  const viewRef = useRef(null);

  // Global header search (NavSearch) works on every route — see useHeaderSearch.
  // Takes priority over a selected node/edge while active, mirroring NarrativeView +
  // RightPanel's own search-first precedence, so the two panels never fight.
  const { searchProp, clearSearch } = useHeaderSearch();

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

  // The Index ranks ALL tracked entities (incl. `other`, which the curated graph
  // nodes often omit) — so the type chips in that view count from the league, not
  // the graph nodes, otherwise the `other` filter never appears.
  const leagueCounts = useMemo(() => {
    const c = {};
    for (const t of TYPES) c[t] = 0;
    for (const e of league) c[e.type] = (c[e.type] || 0) + 1;
    return c;
  }, [league]);
  const chipCounts = view === "table" ? leagueCounts : counts;

  const nodeBySlug = useMemo(
    () => new Map(nodes.map((n) => [n.slug, n])), [nodes]);

  // What's actually on screen at the current type filter / link threshold.
  const shownStats = useMemo(() => {
    const isAff = metric === "affinity";
    const thr = METRICS[metric].levels[level];
    let visNodes = 0;
    for (const n of nodes) if (active[n.type]) visNodes += 1;
    let links = 0;
    for (const e of edges) {
      const pass = isAff ? (e.npmi ?? 0) >= thr : e.weight >= thr;
      if (!pass) continue;
      const s = nodeBySlug.get(e.source), t = nodeBySlug.get(e.target);
      if (!s || !t || !active[s.type] || !active[t.type]) continue;
      links += 1;
    }
    return { nodes: visNodes, links };
  }, [edges, nodes, metric, level, active, nodeBySlug]);

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
  const closePanel = useCallback(() => {
    if (searchProp) clearSearch();
    else clearSelection();
  }, [searchProp, clearSearch, clearSelection]);
  const showPanel = panelOpen || !!searchProp;
  useMobilePanelBack(showPanel, closePanel);
  const focusEntity = useCallback((slug) => {
    const n = nodeBySlug.get(slug);
    if (n) selectNode(n);
  }, [nodeBySlug, selectNode]);

  // ── Mount the active view's D3 renderer ──────────────────────────────────
  useEffect(() => {
    if (view !== "board") return;
    const svgEl = svgRef.current, containerEl = containerRef.current;
    if (!svgEl || !containerEl || nodes.length === 0) return;
    const { controller, destroy } = renderBoard({
      svgEl, containerEl, nodes, edges, counts, minMc, maxMc,
      handlers: { selectNode, selectEdge, clearSelection },
    });
    viewRef.current = controller;
    return () => { destroy(); viewRef.current = null; };
  }, [view, nodes, edges, minMc, maxMc, counts, clearSelection, selectNode, selectEdge]);

  useEffect(() => {
    if (view !== "network") return;
    const svgEl = svgRef.current, containerEl = containerRef.current;
    if (!svgEl || !containerEl || nodes.length === 0) return;
    const { controller, destroy } = renderNetwork({
      svgEl, containerEl, nodes, edges, minMc, maxMc,
      handlers: { selectNode, selectEdge, clearSelection },
    });
    viewRef.current = controller;
    return () => { destroy(); viewRef.current = null; };
  }, [view, nodes, edges, minMc, maxMc, clearSelection, selectNode, selectEdge]);

  // ── Apply threshold / type / search / selection (no relayout) ────────────
  useEffect(() => {
    const v = viewRef.current;
    if (!v) return;
    const { nodeSel, lineSel, hitSel, headSel, cardSel } = v;
    const isNet = v.view === "network";
    const q = query.trim().toLowerCase();

    const selSlug = selected?.kind === "node" ? selected.node.slug : null;
    const selEdgeK = selected?.kind === "edge"
      ? edgeKey(selected.edge.a.slug, selected.edge.b.slug) : null;

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

    // Network prunes nodes that lose all visible edges as the threshold rises (else
    // the canvas fills with dust); board keeps every node parked in its region.
    let nodeShown;
    if (isNet) {
      const visibleDeg = new Map();
      for (const d of (v.simNodes || [])) visibleDeg.set(d.slug, 0);
      lineSel.each((d) => {
        if (passes(d) && typeOn(d.source) && typeOn(d.target)) {
          visibleDeg.set(d.source.slug, (visibleDeg.get(d.source.slug) || 0) + 1);
          visibleDeg.set(d.target.slug, (visibleDeg.get(d.target.slug) || 0) + 1);
        }
      });
      nodeShown = (d) => typeOn(d) && ((visibleDeg.get(d.slug) || 0) > 0 ||
        d.slug === selSlug || (focus && focus.has(d.slug)));
    } else {
      nodeShown = (d) => typeOn(d);
    }
    const dimOpacity = isNet ? 0.12 : 0.14;

    nodeSel
      .style("display", (d) => (nodeShown(d) ? null : "none"))
      .style("opacity", (d) => {
        if (!nodeShown(d)) return null;
        if (focus && !focus.has(d.slug)) return dimOpacity;
        return 1;
      })
      .classed("is-selected", (d) => d.slug === selSlug)
      .classed("is-focus", (d) => !!focus && focus.has(d.slug) && d.slug !== selSlug);

    lineSel
      .style("display", (d) => (edgeShown(d) ? null : "none"))
      .style("opacity", (d) => (focus && !(focus.has(d.source.slug) && focus.has(d.target.slug)) ? 0.06 : null))
      .attr("stroke-width", (d) => (isAff
        ? 0.5 + Math.max(0, d.npmi ?? 0) * 3.2
        : Math.min(0.6 + Math.log2(d.weight) * 0.7, 4)))
      .classed("is-incident", (d) => !!selSlug && (d.source.slug === selSlug || d.target.slug === selSlug))
      .classed("is-active", (d) => edgeKey(d.source.slug, d.target.slug) === selEdgeK);
    hitSel.style("display", (d) => (edgeShown(d) ? null : "none"));

    if (headSel) headSel.classed("is-off", (t) => !active[t]);
    if (cardSel) cardSel.classed("is-off", (t) => !active[t]);
  }, [active, metric, level, query, selected, nodes, neighbors, view]);

  // Re-frame when the lens (metric) changes in the network — the visible set shifts.
  useEffect(() => {
    if (view !== "network") return;
    const id = setTimeout(() => viewRef.current?.reset?.(), 140);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric, view]);

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

  // Search → zoom to the matching region/nodes; lone match auto-selects.
  useEffect(() => {
    const q = query.trim().toLowerCase();
    if (!q) return;
    const matches = nodes.filter(
      (n) => n.name.toLowerCase().includes(q) || n.slug.includes(q));
    if (!matches.length) return;
    const slugs = new Set(matches.map((n) => n.slug));
    const id = setTimeout(() => {
      viewRef.current?.fitTo?.(slugs);
      if (matches.length === 1) {
        const n = nodeBySlug.get(matches[0].slug);
        if (n) selectNode(n);
      }
    }, 250);
    return () => clearTimeout(id);
  }, [query, nodes, nodeBySlug, selectNode]);

  // ── Toolbar handlers ─────────────────────────────────────────────────────
  // Single-select filter: clicking a type narrows to ONLY that type; clicking the
  // sole-active type (or "All") resets to every type. Reads as a radio, not a
  // multi-toggle — "click Protocols → only protocols".
  const allOf = useCallback(() => Object.fromEntries(TYPES.map((t) => [t, true])), []);
  const toggleType = useCallback((t) => setActive((a) => {
    const onlyThis = a[t] && TYPES.every((x) => x === t ? a[x] : !a[x]);
    if (onlyThis) return allOf();                 // already isolated → back to All
    return Object.fromEntries(TYPES.map((x) => [x, x === t]));
  }), [allOf]);
  const allTypes = useCallback(() => setActive(allOf()), [allOf]);
  const recenter = useCallback(() => { viewRef.current?.reset?.(); }, []);
  const zoomIn = useCallback(() => { viewRef.current?.zoomBy?.(1.4); }, []);
  const zoomOut = useCallback(() => { viewRef.current?.zoomBy?.(1 / 1.4); }, []);

  const panelNeighbors = useMemo(() => {
    if (selected?.kind !== "node") return [];
    // Protocol-screener rows carry an `entitySlug` (the brand entity their coverage
    // resolved to, e.g. aave-v3 → aave) — use it so "Most Connected" matches the
    // displayed coverage/links rather than the versioned slug's sparse edges.
    const key = selected.node.entitySlug || selected.node.slug;
    return (neighbors.get(key) || [])
      .slice(0, 12)
      .map((n) => ({ ...nodeBySlug.get(n.slug), weight: n.weight }))
      .filter((n) => n.slug);
  }, [selected, neighbors, nodeBySlug]);

  const empty = nodes.length === 0;

  return (
    <div className="feed-grid">
      <div className="feed-left map-left">
        <div className="view-head map-head">
          <div className="view-head-titles">
            <h1 className="view-title">Atlas</h1>
            <p className="view-sub">
              {empty ? "No graph yet" : view === "table" ? (
                <>{league.length} entities · ranked by coverage · DeFiLlama TVL where listed</>
              ) : <>
                {shownStats.nodes} entities · {shownStats.links} links ·{" "}
                {view === "board"
                  ? <>grouped by type, {metric === "affinity" ? "linked by affinity" : "linked by co-mention volume"}</>
                  : <>force-directed, {metric === "affinity" ? "linked by affinity (co-mention vs chance)" : "linked by co-mention volume"}</>}
              </>}
            </p>
          </div>
          {!empty && view === "table" && (
            <div className="view-head-meta">
              <IndexInfo />
            </div>
          )}
        </div>

        {!empty && (
          <MapToolbar
            view={view} setView={setView}
            metric={metric} setMetric={setMetric}
            level={level} setLevel={setLevel}
            query={query} setQuery={setQuery}
            active={active} counts={chipCounts}
            toggleType={toggleType} allTypes={allTypes}
          />
        )}

        {view === "table" ? (
          <div className="map-canvas map-canvas--table">
            <EntityLeague
              entities={league}
              query={query}
              active={active}
              onSelect={selectNode}
              selectedSlug={selected?.kind === "node" ? selected.node.slug : null}
            />
          </div>
        ) : (
        <div className="map-canvas" ref={containerRef}>
          {empty ? (
            <EmptyState variant="feed" glyph="◈">No entity graph yet.<br />The co-occurrence job builds it from recent feeds.</EmptyState>
          ) : (
            <svg ref={svgRef} className="map-svg" role="img"
              aria-label={view === "board"
                ? "Map of crypto entities grouped by type and linked by co-mention"
                : "Force-directed map of crypto entities linked by co-mention"} />
          )}
          {!empty && (
            <div className="map-zoomctl" role="group" aria-label="Zoom controls">
              <button type="button" className="map-zoom-btn" onClick={zoomIn} aria-label="Zoom in" title="Zoom in">＋</button>
              <button type="button" className="map-zoom-btn" onClick={zoomOut} aria-label="Zoom out" title="Zoom out">－</button>
              <button type="button" className="map-zoom-btn map-zoom-fit" onClick={recenter} aria-label="Reset view" title="Reset view">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
                  strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M2 5.5V2.5h3M14 5.5V2.5h-3M2 10.5v3h3M14 10.5v3h-3" />
                </svg>
              </button>
            </div>
          )}
        </div>
        )}
      </div>

      <div className={`feed-right${showPanel ? " panel-open" : ""}`}>
        {searchProp ? (
          <SearchPanel search={searchProp} />
        ) : (
          <EntityMapPanel
            selected={selected}
            neighbors={panelNeighbors}
            onClose={clearSelection}
            onFocusEntity={focusEntity}
          />
        )}
      </div>
    </div>
  );
}
