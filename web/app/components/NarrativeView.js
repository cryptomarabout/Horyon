"use client";

import { useState, useMemo } from "react";
import RightPanel from "./RightPanel";
import MomentumChip from "./MomentumChip";
import { stateMeta, evidenceCounts } from "../../lib/narratives";
import useHeaderSearch from "../../lib/useHeaderSearch";

function NarrativeCard({ n, selected, onSelect }) {
  const sm = stateMeta(n.state);
  const ev = evidenceCounts(n.signals);
  const ents = (n.entities || []).slice(0, 4);
  return (
    <li
      className={`board-card board-card--${sm.cls}${selected ? " is-selected" : ""}`}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
    >
      <div className="board-card-bar" aria-hidden />
      <div className="board-card-main">
        <div className="board-card-top">
          <span className={`board-state board-state--${sm.cls}`}>
            <span className="board-state-glyph" aria-hidden>{sm.glyph}</span>
            {sm.label}
          </span>
          <MomentumChip rho={n.momentum_ratio} delta={n.delta_48h} state={n.state} />
        </div>
        <h3 className="board-card-title">{n.label}</h3>
        {n.thesis && <p className="board-card-thesis">{n.thesis}</p>}
        <div className="board-card-foot">
          {ents.length > 0 && (
            <span className="board-card-ents">
              {ents.map(e => (
                <span key={e.slug} className="board-ent">
                  {e.logo_url && <img src={e.logo_url} alt="" className="board-ent-logo"
                    onError={imgE => { imgE.currentTarget.style.display = "none"; }} />}
                  {e.name}
                </span>
              ))}
            </span>
          )}
          <span className="board-card-ev">
            {ev.map(c => (
              <span key={c.type} className="board-ev-tally" title={`${c.n} ${c.label}`}>
                <span aria-hidden>{c.glyph}</span>{c.n}
              </span>
            ))}
            <span className="board-ev-total">{n.signal_count} signals</span>
          </span>
        </div>
      </div>
    </li>
  );
}

export default function NarrativeView({ narratives = [] }) {
  const [slug, setSlug] = useState(narratives[0]?.slug ?? null);
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

  // Heating/forming first (already ordered server-side); group label for the board
  const heatingCount = narratives.filter(n => n.state === "heating" || n.state === "forming").length;

  return (
    <div className="feed-grid">
      <div className="feed-left">
        <div className="feed-scroll">
          <div className="view-head">
            <div className="view-head-titles">
              <h1 className="view-title">Active Narratives</h1>
              <p className="view-sub">
                Cross-source story clusters · {narratives.length} tracked
                {heatingCount > 0 && <> · <span className="view-sub-hot">{heatingCount} heating</span></>}
              </p>
            </div>
          </div>

          {narratives.length === 0 ? (
            <div className="feed-empty">
              <div className="feed-empty-glyph" aria-hidden>◈</div>
              <p>No active narratives yet.<br />The clustering job builds these after each digest.</p>
            </div>
          ) : (
            <ul className="board-list">
              {narratives.map(n => (
                <NarrativeCard
                  key={n.slug}
                  n={n}
                  selected={slug === n.slug && !searchProp}
                  onSelect={() => { setSlug(n.slug); clearSearch(); }}
                />
              ))}
            </ul>
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
