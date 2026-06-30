import { select } from "d3-selection";
import { zoom as d3zoom, zoomIdentity } from "d3-zoom";
import { TYPE_META, TYPES, avatarCandidates, radiusFor, monogram, safeId, arcPath } from "../../../lib/entityGraph";
import { wireAvatarFallback } from "./avatar";

// ════════════════════════════════════════════════════════════════════════════
// BOARD view — deterministic, carded "tidy clusters" layout.
// Entities are grouped into one titled region per type, laid out on a responsive
// grid; nodes are packed in a neat grid inside each region. No force simulation →
// always clean, cheap. Frames itself to fit on build so the smallest region
// (e.g. "Other") is never clipped at the canvas edge.
//
// Imperative D3 render, extracted from EntityGraph so the React root stays lean.
// Returns the same `{ controller, destroy }` contract the network renderer uses:
// `controller` is stored on EntityGraph's viewRef (read by the visibility pass);
// `destroy` tears down observers + the SVG on cleanup.
// ════════════════════════════════════════════════════════════════════════════
export function renderBoard({ svgEl, containerEl, nodes, edges, counts, minMc, maxMc, handlers }) {
  const { selectNode, selectEdge, clearSelection } = handlers;

  const simNodes = nodes.map((n) => ({ ...n }));
  const byId = new Map(simNodes.map((n) => [n.slug, n]));
  const simEdges = edges
    .filter((e) => byId.has(e.source) && byId.has(e.target))
    .map((e) => ({ ...e }));
  // Types present in the data, in canonical order → one region each.
  const presentTypes = TYPES.filter((t) => simNodes.some((n) => n.type === t));

  const svg = select(svgEl);
  svg.selectAll("*").remove();

  const bg = svg.append("rect").attr("class", "map-bg").on("click", clearSelection);
  const zoomRoot = svg.append("g").attr("class", "map-zoom");
  const regionG = zoomRoot.append("g").attr("class", "map-regions");
  const edgeG = zoomRoot.append("g").attr("class", "map-edges");
  const edgeHitG = zoomRoot.append("g").attr("class", "map-edges-hit");
  const nodeG = zoomRoot.append("g").attr("class", "map-nodes");
  const headerG = zoomRoot.append("g").attr("class", "map-headers");

  // Region card + header per present type (positions filled in by relayout).
  const cardSel = regionG.selectAll("rect").data(presentTypes).join("rect")
    .attr("class", (t) => `map-region-card ty-${t}`).attr("rx", 14);
  const headSel = headerG.selectAll("g").data(presentTypes).join("g")
    .attr("class", (t) => `map-region-head ty-${t}`);
  // Leading colour swatch (matches the legend chips) so each region is keyed to
  // its type at a glance, then the title + entity count.
  headSel.append("circle").attr("class", "map-region-swatch")
    .attr("r", 3.5).attr("cx", 3).attr("cy", -4);
  headSel.append("text").attr("class", "map-region-title").attr("x", 13)
    .text((t) => (TYPE_META[t]?.label || t).toUpperCase());
  headSel.append("text").attr("class", "map-region-count")
    .text((t) => counts[t]);

  const lineSel = edgeG.selectAll("path").data(simEdges).join("path")
    .attr("class", "map-edge");
  const hitSel = edgeHitG.selectAll("path").data(simEdges).join("path")
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
    .style("cursor", "pointer")
    .on("click", (event, d) => { event.stopPropagation(); selectNode(d); });

  nodeSel.append("title").text((d) =>
    `${d.name} · ${TYPE_META[d.type]?.label?.replace(/s$/, "") || d.type} · ${d.mentionCount} mentions · ${d.degree} links`);
  nodeSel.append("circle").attr("class", "map-node-halo");
  nodeSel.append("circle").attr("class", "map-node-circle");
  nodeSel.append("text").attr("class", "map-node-mono")
    .attr("text-anchor", "middle").attr("dy", "0.34em")
    .text((d) => monogram(d.name));

  // Avatar = mirrored logo → real logo → Twitter pic → (remove → monogram shows).
  nodeSel.filter((d) => avatarCandidates(d).length > 0).each(function (d) {
    const cands = avatarCandidates(d);
    const g = select(this), cid = safeId(d.slug);
    g.append("clipPath").attr("id", cid).append("circle").attr("class", "map-clip-circle");
    const img = g.append("image").attr("class", "map-node-logo").attr("href", cands[0])
      .attr("clip-path", `url(#${cid})`).attr("preserveAspectRatio", "xMidYMid slice");
    wireAvatarFallback(img, g, cands);
  });

  // No resting labels — coins are packed tight, so labels would collide into mush.
  // They reveal on hover / focus / selection (CSS), and the <title> gives a tooltip.
  nodeSel.append("text").attr("class", "map-node-label")
    .attr("text-anchor", "middle")
    .style("opacity", 0)
    .text((d) => d.name);

  // ── Deterministic layout ──────────────────────────────────────────────────
  let regions = {};       // type → { x, y, w, h }
  const HEAD_H = 30, GAP = 10;
  // Each region needs room for its header + at least ~1.5 rows of coins, or the
  // smallest type ("Other") gets a sliver too short for its packed nodes and they
  // spill past the card. We floor every region's height and let the board grow
  // taller than the canvas if needed — fitView then frames the whole thing.
  const MIN_RH = HEAD_H + 64;

  // Size each region proportionally to its entity count (a lightweight treemap):
  // types are balanced across rows, row heights ∝ row totals, in-row widths ∝ count.
  // So Protocols (the biggest) gets a big block and small types don't waste space.
  const computeRegions = (W, H) => {
    const M = 6;                                    // outer margin so nothing touches edges
    const IW = W - 2 * M;
    const types = [...presentTypes].sort((a, b) => (counts[b] || 0) - (counts[a] || 0));
    const total = types.reduce((s, t) => s + (counts[t] || 1), 0) || 1;
    const out = {};
    if (W < 560 || types.length <= 1) {           // narrow → full-width stacked rows
      let y = M;
      types.forEach((t) => {
        const h = Math.max(MIN_RH, (H - 2 * M) * ((counts[t] || 1) / total));
        out[t] = { x: M, y, w: IW, h }; y += h;
      });
      return out;
    }
    const R = types.length <= 3 ? 1 : 2;
    const rows = Array.from({ length: R }, () => ({ types: [], sum: 0 }));
    types.forEach((t) => {                          // greedy: feed the lightest row
      const r = rows.reduce((a, b) => (a.sum <= b.sum ? a : b));
      r.types.push(t); r.sum += (counts[t] || 1);
    });
    // Row heights ∝ row totals, but floored so a light row still fits its coins;
    // total inner height grows past the canvas when needed (fitView reframes).
    const rowSums = rows.map((r) => r.sum || 1);
    const rawIH = (H - 2 * M);
    const rowH = rowSums.map((s) => Math.max(MIN_RH, rawIH * (s / total)));
    let y = M;
    rows.forEach((row, ri) => {
      const h = rowH[ri];
      let x = M;
      row.types.forEach((t) => {
        const w = IW * ((counts[t] || 1) / row.sum);
        out[t] = { x, y, w, h }; x += w;
      });
      y += h;
    });
    return out;
  };

  const packRegion = (t) => {
    const { x: rx, y: ry, w: rw, h: rh } = regions[t];
    const tNodes = simNodes
      .filter((n) => n.type === t)
      .sort((a, b) => (b.mentionCount || 0) - (a.mentionCount || 0));
    const n = tNodes.length;
    const ax = rx + GAP + 4, ay = ry + HEAD_H;
    const aw = Math.max(40, rw - 2 * (GAP + 4));
    const ah = Math.max(40, rh - HEAD_H - GAP - 4);
    const colsIn = Math.max(1, Math.round(Math.sqrt(n * (aw / ah))) || 1);
    const rowsIn = Math.max(1, Math.ceil(n / colsIn));
    const cw = aw / colsIn, ch = ah / rowsIn;
    const cap = Math.min(cw, ch) * 0.4;             // coins fill ~80% of the cell → gaps
    tNodes.forEach((node, i) => {
      const col = i % colsIn, row = Math.floor(i / colsIn);
      node.__x = ax + cw * (col + 0.5);
      node.__y = ay + ch * (row + 0.5);
      node.__r = radiusFor(node.mentionCount, minMc, maxMc, cap);
    });
  };

  const computeLayout = (W, H) => {
    regions = computeRegions(W, H);
    presentTypes.forEach(packRegion);
  };

  const applyLayout = () => {
    cardSel.attr("x", (t) => regions[t].x + GAP / 2).attr("y", (t) => regions[t].y + GAP / 2)
      .attr("width", (t) => regions[t].w - GAP).attr("height", (t) => regions[t].h - GAP);
    headSel.attr("transform", (t) => `translate(${regions[t].x + GAP + 4},${regions[t].y + 19})`);
    headSel.select(".map-region-count").attr("x", (t) =>
      (TYPE_META[t]?.label || t).length * 7.2 + 21);

    nodeSel.attr("transform", (d) => `translate(${d.__x},${d.__y})`);
    nodeSel.select("circle.map-node-halo").attr("r", (d) => d.__r + Math.min(5, d.__r * 0.35));
    nodeSel.select("circle.map-node-circle").attr("r", (d) => d.__r);
    nodeSel.select("text.map-node-mono").style("font-size", (d) => `${Math.max(7, d.__r * 0.62)}px`);
    nodeSel.select("text.map-node-label").attr("dy", (d) => d.__r + 11);
    nodeSel.select("image.map-node-logo")
      .attr("x", (d) => -(d.__r - 1.5)).attr("y", (d) => -(d.__r - 1.5))
      .attr("width", (d) => (d.__r - 1.5) * 2).attr("height", (d) => (d.__r - 1.5) * 2);
    nodeSel.select("clipPath circle.map-clip-circle").attr("r", (d) => Math.max(2, d.__r - 1.5));

    const ePath = (d) => {
      const a = byId.get(d.source), b = byId.get(d.target);
      return a && b ? arcPath(a.__x, a.__y, b.__x, b.__y) : "";
    };
    lineSel.attr("d", (d) => (d.__d = ePath(d)));
    hitSel.attr("d", (d) => d.__d);
  };

  // Zoom / pan.
  let userZoomed = false;
  const zoomB = d3zoom().scaleExtent([0.3, 6])
    .on("zoom", (event) => {
      zoomRoot.attr("transform", event.transform);
      if (event.sourceEvent) userZoomed = true;
    });
  svg.call(zoomB).on("dblclick.zoom", null);

  // Frame the (only the *visible*) nodes inside the viewport with padding. The
  // board can lay out taller than the canvas, so we fit on build — this is what
  // keeps the bottom-most region from being clipped.
  const fitView = (force = false, only = null) => {
    if (userZoomed && !force) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodeSel.each(function (n) {
      if (this.style.display === "none") return;
      if (only && !only.has(n.slug)) return;
      minX = Math.min(minX, n.__x - n.__r); maxX = Math.max(maxX, n.__x + n.__r);
      minY = Math.min(minY, n.__y - n.__r); maxY = Math.max(maxY, n.__y + n.__r);
    });
    if (!isFinite(minX)) return;
    const cw = containerEl.clientWidth, ch = containerEl.clientHeight;
    // Pad a touch extra at the top/bottom so region titles + halos clear the edge.
    const gw = maxX - minX || 1, gh = (maxY - minY) + 40 || 1, pad = 56;
    const scale = Math.max(0.3, Math.min((cw - pad) / gw, (ch - pad) / gh, 4));
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const t = zoomIdentity.translate(cw / 2, ch / 2).scale(scale).translate(-cx, -cy);
    svg.call(zoomB.transform, t);
  };

  let W = containerEl.clientWidth || 960, H = containerEl.clientHeight || 640;
  const relayout = () => {
    W = containerEl.clientWidth || W; H = containerEl.clientHeight || H;
    bg.attr("x", -W * 2).attr("y", -H * 2).attr("width", W * 5).attr("height", H * 5);
    computeLayout(W, H);
    applyLayout();
    fitView();               // frame the whole board → "Other" never clipped
  };
  relayout();
  svgEl.classList.add("is-ready");

  const controller = {
    view: "board",
    svg, zoomB, nodeSel, lineSel, hitSel, headSel, cardSel,
    reset: () => { userZoomed = false; fitView(true); },
    fitTo: (slugs) => fitView(true, slugs instanceof Set ? slugs : new Set(slugs)),
    zoomBy: (k) => { userZoomed = true; svg.call(zoomB.scaleBy, k); },
  };

  const ro = new ResizeObserver(() => {
    const nw = containerEl.clientWidth, nh = containerEl.clientHeight;
    if (!nw || !nh || (nw === W && nh === H)) return;
    relayout();
  });
  ro.observe(containerEl);

  const destroy = () => {
    ro.disconnect();
    svg.on(".zoom", null); svg.selectAll("*").remove();
    svgEl.classList.remove("is-ready");
  };

  return { controller, destroy };
}
