import {
  forceSimulation, forceLink, forceManyBody,
  forceCenter, forceCollide, forceX, forceY,
} from "d3-force";
import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity } from "d3-zoom";
import { drag as d3drag } from "d3-drag";
import { TYPE_META, avatarCandidates, radiusFor, monogram, safeId, R_MIN_NET, R_MAX_NET } from "../../../lib/entityGraph";
import { wireAvatarFallback } from "./avatar";

// ════════════════════════════════════════════════════════════════════════════
// NETWORK view — force-directed graph; relationships drive position. Drag-able.
//
// Imperative D3 render extracted from EntityGraph. Returns the shared
// `{ controller, destroy }` contract — `controller` (stored on viewRef, read by
// the visibility pass) carries `simNodes` so the threshold pass can prune nodes
// that lose all visible edges; `destroy` stops the sim + tears down the SVG.
// ════════════════════════════════════════════════════════════════════════════
export function renderNetwork({ svgEl, containerEl, nodes, edges, minMc, maxMc, handlers }) {
  const { selectNode, selectEdge, clearSelection } = handlers;

  let w = containerEl.clientWidth || 900;
  let h = containerEl.clientHeight || 640;

  const simNodes = nodes.map((n) => ({
    ...n, r: radiusFor(n.mentionCount, minMc, maxMc, null, R_MIN_NET, R_MAX_NET),
  }));
  const byId = new Map(simNodes.map((n) => [n.slug, n]));
  const simEdges = edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => ({ ...e }));

  const svg = select(svgEl);
  svg.selectAll("*").remove();

  const bg = svg.append("rect").attr("class", "map-bg")
    .attr("x", 0).attr("y", 0).attr("width", w).attr("height", h)
    .on("click", clearSelection);

  const zoomRoot = svg.append("g").attr("class", "map-zoom");
  const edgeG = zoomRoot.append("g").attr("class", "map-edges");
  const edgeHitG = zoomRoot.append("g").attr("class", "map-edges-hit");
  const nodeG = zoomRoot.append("g").attr("class", "map-nodes");

  const lineSel = edgeG.selectAll("line").data(simEdges).join("line")
    .attr("class", "map-edge");
  const hitSel = edgeHitG.selectAll("line").data(simEdges).join("line")
    .attr("class", "map-edge-hit")
    .on("mouseenter", (event, d) => lineSel.filter((x) => x === d).classed("is-hover", true))
    .on("mouseleave", (event, d) => lineSel.filter((x) => x === d).classed("is-hover", false))
    .on("click", (event, d) => {
      event.stopPropagation();
      lineSel.filter((x) => x === d).classed("is-hover", false);
      selectEdge({ a: d.source, b: d.target, weight: d.weight, npmi: d.npmi, examples: d.examples });
    });

  const nodeSel = nodeG.selectAll("g").data(simNodes).join("g")
    .attr("class", (d) => `map-node ty-${d.type}${d.narrativeState ? " map-node--narr" : ""}`)
    .style("cursor", "pointer");

  nodeSel.append("title").text((d) =>
    `${d.name} · ${TYPE_META[d.type]?.label?.replace(/s$/, "") || d.type} · ${d.mentionCount} mentions · ${d.degree} links`);
  nodeSel.append("circle").attr("class", "map-node-halo").attr("r", (d) => d.r + Math.min(5, d.r * 0.35));
  nodeSel.append("circle").attr("class", "map-node-circle").attr("r", (d) => d.r);

  nodeSel.append("text").attr("class", "map-node-mono")
    .attr("text-anchor", "middle").attr("dy", "0.34em")
    .style("font-size", (d) => `${Math.max(7, d.r * 0.62)}px`)
    .text((d) => monogram(d.name));

  // Avatar = mirrored logo → real logo → Twitter pic → (remove → monogram shows).
  nodeSel.filter((d) => avatarCandidates(d).length > 0).each(function (d) {
    const cands = avatarCandidates(d);
    const g = select(this), cid = safeId(d.slug);
    g.append("clipPath").attr("id", cid).append("circle").attr("r", d.r - 1.5);
    const img = g.append("image").attr("class", "map-node-logo").attr("href", cands[0])
      .attr("x", -(d.r - 1.5)).attr("y", -(d.r - 1.5))
      .attr("width", (d.r - 1.5) * 2).attr("height", (d.r - 1.5) * 2)
      .attr("clip-path", `url(#${cid})`).attr("preserveAspectRatio", "xMidYMid slice");
    wireAvatarFallback(img, g, cands);
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

  nodeSel.on("click", (event, d) => { event.stopPropagation(); selectNode(d); });

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

  // Frame the graph in the viewport (visible nodes — or a subset — with padding).
  const fitView = (force = false, only = null) => {
    if ((userZoomed && !force) || !simNodes.length) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodeSel.each(function (n) {
      if (this.style.display === "none") return;
      if (only && !only.has(n.slug)) return;
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
  svgEl.classList.add("is-ready");

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

  const controller = {
    view: "network",
    svg, zoomB, nodeSel, lineSel, hitSel, simNodes,
    reset: () => fitView(true),
    fitTo: (slugs) => fitView(true, slugs instanceof Set ? slugs : new Set(slugs)),
    zoomBy: (k) => { userZoomed = true; svg.call(zoomB.scaleBy, k); },
  };

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

  const destroy = () => {
    ro.disconnect(); sim.stop();
    svg.on(".zoom", null); svg.selectAll("*").remove();
    svgEl.classList.remove("is-ready");
  };

  return { controller, sim, destroy };
}
