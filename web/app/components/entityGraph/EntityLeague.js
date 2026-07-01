"use client";

import { useState, useMemo } from "react";
import { avatarCandidates, monogram, TYPE_META } from "../../../lib/entityGraph";
import { trajectoryMeta } from "../../../lib/narratives";
import { fmtTvl, fmtPct } from "../../../lib/format";

// ── Entity league — the Atlas "Index" screener ───────────────────────────────
// A ranked, sortable table of ALL tracked entities (not just protocols): coverage
// (mentions) + narrative trajectory + co-mention centrality are universal; DeFiLlama
// TVL/flows (aggregated to the brand server-side) layer on where they exist. Rows are
// node-shaped, so a click reuses the shared entity detail panel. The type filter is
// the same `active` map the Board/Network use (shared TypeChips in the toolbar).

// Compact avatar (logo → twitter → monogram) mirroring the panel cascade.
function LeagueAvatar({ node }) {
  const cands = avatarCandidates(node);
  return (
    <span className="pl-avatar" aria-hidden>
      <span className="pl-avatar-mono">{monogram(node.name)}</span>
      {cands.length > 0 && (
        <img
          className="pl-avatar-img" src={cands[0]} alt="" data-i="0" data-r="0"
          onLoad={(e) => {
            const m = e.currentTarget.previousElementSibling;
            if (m) m.style.visibility = "hidden";
          }}
          onError={(e) => {
            const el = e.currentTarget;
            const i = Number(el.dataset.i) + 1;
            if (i < cands.length) { el.dataset.i = String(i); el.src = cands[i]; }
            else el.style.display = "none";
          }}
        />
      )}
    </span>
  );
}

// One small neighbour avatar (logo → twitter → monogram) for the Connections stack.
function ConnAvatar({ node }) {
  const cands = avatarCandidates(node);
  return (
    <span className={`pl-conn-av eg-avatar--${node.type}`} title={node.name}>
      <span className="pl-conn-mono">{monogram(node.name)}</span>
      {cands.length > 0 && (
        <img
          className="pl-conn-img" src={cands[0]} alt="" data-i="0"
          onLoad={(e) => { const m = e.currentTarget.previousElementSibling; if (m) m.style.visibility = "hidden"; }}
          onError={(e) => {
            const el = e.currentTarget;
            const i = Number(el.dataset.i) + 1;
            if (i < cands.length) { el.dataset.i = String(i); el.src = cands[i]; }
            else el.style.display = "none";
          }}
        />
      )}
    </span>
  );
}

// Avatar stack of the top co-mentioned entities + the TOTAL connection count.
// `degree` is the entity's full co-mention degree (genuinely in the hundreds for
// hubs like Bitcoin), so we render it as a plain total — NOT a "+N more avatars"
// badge, which misread as "+405 more logos".
function ConnStack({ items, degree }) {
  if (!items?.length) return <span className="pl-dim">—</span>;
  const shown = items.slice(0, 4);
  const total = degree || items.length;
  return (
    <span className="pl-conns">
      {shown.map((c) => <ConnAvatar key={c.slug} node={c} />)}
      {total > 0 && (
        <span className="pl-conn-n" title={`${total} co-mentioned entities`}>{total}</span>
      )}
    </span>
  );
}

// Sortable header cell.
function Th({ id, label, sort, setSort, align = "right", title }) {
  const active = sort.key === id;
  return (
    <button
      type="button"
      className={`pl-th pl-th--${align}${active ? " is-active" : ""}`}
      title={title}
      onClick={() =>
        setSort((s) =>
          s.key === id ? { key: id, dir: s.dir === "desc" ? "asc" : "desc" } : { key: id, dir: "desc" })
      }
    >
      {label}
      <span className="pl-th-caret" aria-hidden>
        {active ? (sort.dir === "desc" ? "▾" : "▴") : ""}
      </span>
    </button>
  );
}

// Direction class for a signed % (7d TVL move).
const d7dir = (v) => (v > 0 ? "up" : v < 0 ? "dn" : "flat");

const SORTS = {
  coverage: (a, b) => (b.mentionCount ?? 0) - (a.mentionCount ?? 0),
  horyon:   (a, b) => (b.digestMentionCount ?? 0) - (a.digestMentionCount ?? 0),
  links:    (a, b) => (b.degree ?? 0) - (a.degree ?? 0),
  tvl:      (a, b) => (b.tvl ?? -1) - (a.tvl ?? -1),
  flow7d:   (a, b) => (b.tvlChange7d ?? -1e9) - (a.tvlChange7d ?? -1e9),
};

export default function EntityLeague({ entities = [], query = "", active = null, onSelect, selectedSlug }) {
  // Coverage is the universal ranking — TVL only applies to a subset, so it would
  // sink every non-protocol to the bottom as a default.
  const [sort, setSort] = useState({ key: "coverage", dir: "desc" });

  const q = query.trim().toLowerCase();
  const rows = useMemo(() => {
    let r = entities;
    if (active) r = r.filter((e) => active[e.type] !== false);
    if (q) {
      r = r.filter(
        (e) =>
          e.name.toLowerCase().includes(q) ||
          e.slug.includes(q) ||
          (e.tokenSymbol || "").toLowerCase().includes(q) ||
          (e.category || "").toLowerCase().includes(q)
      );
    }
    const cmp = SORTS[sort.key] || SORTS.coverage;
    r = [...r].sort(cmp);
    if (sort.dir === "asc") r.reverse();
    return r;
  }, [entities, active, q, sort]);

  const maxTvl = useMemo(() => Math.max(1, ...entities.map((e) => e.tvl || 0)), [entities]);
  const maxCov = useMemo(() => Math.max(1, ...entities.map((e) => e.mentionCount || 0)), [entities]);
  const maxHoryon = useMemo(
    () => Math.max(1, ...entities.map((e) => e.digestMentionCount || 0)),
    [entities]
  );
  // Log scale for the TVL fill so mid-cap entities still read against the giants.
  const tvlFill = (v) => (v > 0 ? Math.max(4, (Math.log10(v) / Math.log10(maxTvl)) * 100) : 0);

  if (!entities.length) {
    return <div className="pl-empty">No entities yet — the co-occurrence job builds this from recent feeds.</div>;
  }

  return (
    <div className="pl-wrap">
      <div className="pl-scroll">
        <table className="pl-table">
          <thead>
            <tr>
              <th className="pl-rank-h" scope="col">#</th>
              <th className="pl-name-h" scope="col">Entity</th>
              <th className="pl-type-h pl-hide-sm" scope="col">Type</th>
              <th scope="col" className="pl-tvl-h">
                <span className="pl-th-pair">
                  <Th id="tvl" label="TVL" sort={sort} setSort={setSort} title="DeFiLlama TVL, aggregated to the brand (protocols + tracked chains)" />
                  <Th id="flow7d" label="7d" sort={sort} setSort={setSort} title="Change in TVL over the last 7 days" />
                </span>
              </th>
              <th scope="col"><Th id="coverage" label="Sources" sort={sort} setSort={setSort} align="left" title="Mentions across ~107 ingested sources (raw coverage)" /></th>
              <th scope="col"><Th id="horyon" label="Horyon" sort={sort} setSort={setSort} align="left" title="Distinct Horyon daily-brief bullets that cited this entity (curated coverage) + narrative trajectory" /></th>
              <th scope="col" className="pl-conn-h"><Th id="links" label="Connected" sort={sort} setSort={setSort} align="left" title="Most co-mentioned entities + total co-mention degree" /></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p, i) => {
              const traj = p.narrativeState ? trajectoryMeta(p.narrativeState) : null;
              const covW = Math.max(4, ((p.mentionCount || 0) / maxCov) * 100);
              const horyonW = p.digestMentionCount ? Math.max(4, (p.digestMentionCount / maxHoryon) * 100) : 0;
              return (
                <tr
                  key={p.slug}
                  className={`pl-row${selectedSlug === p.slug ? " is-selected" : ""}`}
                  onClick={() => onSelect?.(p)}
                  tabIndex={0}
                  role="button"
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect?.(p); } }}
                >
                  <td className="pl-rank">{i + 1}</td>
                  <td className="pl-name">
                    <LeagueAvatar node={p} />
                    <span className="pl-name-txt">
                      <span className="pl-name-main">
                        {p.name}
                        {p.tokenSymbol && <span className="pl-ticker">{p.tokenSymbol}</span>}
                      </span>
                      <span className="pl-name-sub">
                        {p.category || (TYPE_META[p.type]?.label?.replace(/s$/, "") || "Entity")}
                        {p.chains?.length ? <span className="pl-chains"> · {p.chains.length} chain{p.chains.length > 1 ? "s" : ""}</span> : null}
                      </span>
                    </span>
                  </td>
                  <td className="pl-type pl-hide-sm">
                    <span className={`pl-type-dot egdot--${p.type}`} aria-hidden />
                    <span className="pl-type-lbl">{TYPE_META[p.type]?.label?.replace(/s$/, "") || "Entity"}</span>
                  </td>
                  <td className="pl-num pl-tvl">
                    {p.tvl ? (
                      <>
                        <i className="pl-tvl-fill" style={{ width: `${tvlFill(p.tvl)}%` }} aria-hidden />
                        <span className="pl-tvl-nums">
                          <span className="pl-tvl-val">{fmtTvl(p.tvl)}</span>
                          {p.tvlChange7d != null && (
                            <span className={`pl-tvl-d7 pl-tvl-d7--${d7dir(p.tvlChange7d)}`}>
                              {fmtPct(p.tvlChange7d) ?? "—"}
                            </span>
                          )}
                        </span>
                      </>
                    ) : <span className="pl-dim">—</span>}
                  </td>
                  <td className="pl-cover">
                    <span className="pl-cover-wrap">
                      <span className="pl-cov-bar-wrap" aria-hidden>
                        <i className="pl-cov-bar" style={{ width: `${covW}%` }} />
                      </span>
                      <span className="pl-mentions">{p.mentionCount}</span>
                    </span>
                  </td>
                  <td className="pl-cover pl-horyon">
                    <span className="pl-cover-wrap">
                      <span className="pl-cov-bar-wrap" aria-hidden>
                        <i className="pl-cov-bar pl-cov-bar--horyon" style={{ width: `${horyonW}%` }} />
                      </span>
                      <span className={`pl-mentions${p.digestMentionCount ? "" : " pl-mentions-u"}`}>
                        {p.digestMentionCount || 0}
                      </span>
                      {traj && (
                        <span className={`pl-traj pl-traj--${traj.cls}`} title={`Narrative: ${traj.label}`}>
                          {traj.label}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="pl-conn">
                    <ConnStack items={p.connections} degree={p.degree} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && <div className="pl-empty">No entities match this filter.</div>}
      </div>
    </div>
  );
}
