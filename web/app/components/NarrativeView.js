"use client";

import { useState, useMemo, useEffect } from "react";
import RightPanel from "./RightPanel";
import Sparkline from "./research/Sparkline";
import {
  trajectoryMeta, momentumMultiple, convictionTier, coverageWindow,
  asOfLabel, evidenceMixLabel, cadenceSeries, cadenceLabels,
} from "../../lib/narratives";
import { dedupeEntities, EntityAvatar } from "./EntityTag";
import useHeaderSearch from "../../lib/useHeaderSearch";
import useMobilePanelBack from "../../lib/useMobilePanelBack";
import EmptyState from "./ui/EmptyState";

// Trajectory tag — the institutional replacement for the emoji momentum state.
// A typographic caret + a desk word (Accelerating / Established / Moderating …).
function TrajectoryTag({ state }) {
  const tm = trajectoryMeta(state);
  const caret = tm.dir === "up" ? "▲" : tm.dir === "down" ? "▼" : "—";
  return (
    <span className={`rsrch-traj rsrch-traj--${tm.cls}`}>
      <span className="rsrch-traj-caret" aria-hidden>{caret}</span>
      {tm.label}
    </span>
  );
}

function CoverageAvatars({ entities, limit }) {
  const ents = dedupeEntities((entities || []).slice(0, 8)).slice(0, limit);
  if (!ents.length) return null;
  return (
    <span className="rsrch-coverage" aria-label="Entities covered">
      {ents.map(e => (
        <span key={e.slug} className="rsrch-cov-pill">
          <EntityAvatar
            avatars={[e.avatar_cached ? `/api/avatar/${e.slug}` : null, e.logo_url].filter(Boolean)}
            type={e.type || "other"}
            name={e.display}
            imgClass="rsrch-cov-logo"
            monoClass="rsrch-cov-mono"
          />
          <span className="rsrch-cov-name">{e.display}</span>
        </span>
      ))}
    </span>
  );
}

// A research-index entry. `lead` renders the top brief larger with a taller sparkline.
// All items show the activity sparkline — lead at full size, others compact.
function BriefRow({ n, selected, onSelect, lead }) {
  const cov = coverageWindow(n.first_seen, n.last_signal_at);
  const conv = convictionTier({ signalCount: n.signal_count, spanDays: cov?.spanDays || 0 });
  const mult = momentumMultiple(n.momentum_ratio);
  const cadence = cadenceSeries(n.signals, n.first_seen);
  const labels = cadenceLabels(n.signals, n.first_seen);
  const mix = evidenceMixLabel(n.signals);

  return (
    <li
      className={`rsrch-item${lead ? " rsrch-item--lead" : ""}${selected ? " is-selected" : ""}`}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
    >
      <div className="rsrch-item-eyebrow">
        <span className="rsrch-sector">{n.sector}</span>
        <TrajectoryTag state={n.state} />
      </div>

      <h3 className="rsrch-item-title">{n.label}</h3>
      {n.thesis && <p className="rsrch-item-dek">{n.thesis}</p>}

      {cadence?.length > 0 && (
        <div className="rsrch-item-spark">
          <Sparkline
            data={cadence}
            labels={labels}
            vbWidth={lead ? 320 : 200}
            height={lead ? 26 : 18}
          />
          {lead && mix && <span className="rsrch-item-mix">{mix}</span>}
        </div>
      )}

      <div className="rsrch-item-meta">
        <CoverageAvatars entities={n.entities} limit={lead ? 5 : 3} />
        <span className="rsrch-stats">
          {cov && <span className="rsrch-stat">{cov.sinceLabel}</span>}
          <span className="rsrch-stat rsrch-stat--strong">{n.signal_count} developments</span>
          <span className={`rsrch-conv rsrch-conv--${conv.key}`}>{conv.label} conviction</span>
          {mult && <span className="rsrch-stat rsrch-mult" title="Momentum ρ — 48h activity vs 21-day baseline">ρ {mult}</span>}
        </span>
      </div>
    </li>
  );
}

export default function NarrativeView({ narratives = [] }) {
  // Start with no selection so the panel doesn't auto-open on mobile; on desktop
  // (>900px) pre-select the lead brief after mount (mirrors the prior behaviour).
  const [slug, setSlug] = useState(null);
  const [sector, setSector] = useState(null);
  useEffect(() => {
    if (narratives.length && !window.matchMedia("(max-width: 900px)").matches) {
      setSlug(narratives[0].slug);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { searchProp, clearSearch } = useHeaderSearch();

  const selected = useMemo(
    () => narratives.find(n => n.slug === slug) || null,
    [narratives, slug]
  );

  const panelOpen = !!selected || !!searchProp;
  const handleClose = () => {
    if (searchProp) clearSearch();
    else setSlug(null);
  };
  useMobilePanelBack(panelOpen, handleClose);

  // Sector taxonomy for the filter strip (count per sector, busiest first).
  const sectors = useMemo(() => {
    const tally = new Map();
    for (const n of narratives) {
      const s = n.sector || "DeFi";
      tally.set(s, (tally.get(s) || 0) + 1);
    }
    return [...tally.entries()].sort((a, b) => b[1] - a[1]);
  }, [narratives]);

  const shown = useMemo(
    () => (sector ? narratives.filter(n => (n.sector || "DeFi") === sector) : narratives),
    [narratives, sector]
  );

  // Freshness: most recent signal across the whole desk.
  const updated = useMemo(() => {
    const latest = narratives.reduce((a, n) => (n.last_signal_at > a ? n.last_signal_at : a), "");
    return latest ? asOfLabel(latest) : null;
  }, [narratives]);

  return (
    <div className="feed-grid">
      <div className="feed-left">
        <div className="feed-scroll">
          <div className="view-head rsrch-head">
            <div className="view-head-titles">
              <h1 className="view-title rsrch-masthead">Research</h1>
              <p className="view-sub">Independent intelligence on the themes moving digital-asset markets</p>
            </div>
            {narratives.length > 0 && (
              <div className="view-head-meta">
                <span className="digest-bar-signals">{narratives.length} in coverage</span>
                {updated && (
                  <>
                    <span className="digest-bar-dot">·</span>
                    <span className="rsrch-updated">{updated}</span>
                  </>
                )}
              </div>
            )}
          </div>

          {narratives.length === 0 ? (
            <EmptyState variant="feed" glyph="◆">
              No briefs in coverage yet.<br />The research pipeline compiles these after each digest.
            </EmptyState>
          ) : (
            <>
              {sectors.length > 1 && (
                <div className="rsrch-filter" role="tablist" aria-label="Filter by sector">
                  <button
                    role="tab"
                    aria-selected={!sector}
                    className={`rsrch-filter-chip${!sector ? " is-active" : ""}`}
                    onClick={() => setSector(null)}
                  >
                    All <span className="rsrch-filter-n">{narratives.length}</span>
                  </button>
                  {sectors.map(([s, c]) => (
                    <button
                      key={s}
                      role="tab"
                      aria-selected={sector === s}
                      className={`rsrch-filter-chip${sector === s ? " is-active" : ""}`}
                      onClick={() => setSector(s === sector ? null : s)}
                    >
                      {s} <span className="rsrch-filter-n">{c}</span>
                    </button>
                  ))}
                </div>
              )}

              <ul className="rsrch-list">
                {shown.map((n, i) => (
                  <BriefRow
                    key={n.slug}
                    n={n}
                    lead={i === 0}
                    selected={slug === n.slug && !searchProp}
                    onSelect={() => { setSlug(n.slug); clearSearch(); }}
                  />
                ))}
              </ul>
            </>
          )}
        </div>
      </div>

      <div className={`feed-right${panelOpen ? " panel-open" : ""}`}>
        <RightPanel
          narrative={searchProp ? null : selected}
          search={searchProp}
          onClose={handleClose}
        />
      </div>
    </div>
  );
}
