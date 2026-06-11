"use client";

import { useState, useMemo } from "react";
import RightPanel from "./RightPanel";
import useHeaderSearch from "../../lib/useHeaderSearch";

const MO = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const ROT = {
  BTC:   { glyph: "₿", cls: "rot-btc",   label: "Bitcoin week" },
  ETH:   { glyph: "Ξ", cls: "rot-eth",   label: "Ethereum week" },
  ALT:   { glyph: "◈", cls: "rot-alt",   label: "Altcoin week" },
  MIXED: { glyph: "≋", cls: "rot-mixed", label: "Mixed week" },
};

function fmtWeek(s, e) {
  if (!s || !e) return "";
  const [y, ms, ds] = s.split("-").map(Number);
  const [, me, de] = e.split("-").map(Number);
  return ms === me
    ? `${MO[ms - 1]} ${ds}–${de}, ${y}`
    : `${MO[ms - 1]} ${ds} – ${MO[me - 1]} ${de}, ${y}`;
}

// First "Key Stories" headline (📰 section) for a one-line preview, if present.
function previewLine(content) {
  if (!content) return null;
  const lines = content.split("\n").map(l => l.trim());
  const idx = lines.findIndex(l => /^<b>📰/.test(l));
  if (idx < 0) return null;
  for (let i = idx + 1; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith("•")) {
      return l.slice(1).replace(/<[^>]*>/g, "").replace(/&[a-z#0-9]+;/gi, " ").trim().slice(0, 110);
    }
    if (/^<b>/.test(l)) break;
  }
  return null;
}

function WeeklyCard({ w, selected, onSelect }) {
  const rot = ROT[w.rotation] || ROT.MIXED;
  const preview = useMemo(() => previewLine(w.content), [w.content]);
  return (
    <li
      className={`board-card board-card--weekly${selected ? " is-selected" : ""}`}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={e => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
    >
      <div className="board-card-bar" aria-hidden />
      <div className="board-card-main">
        <div className="board-card-top">
          <span className="weekly-badge">
            <span className={`weekly-badge-glyph ${rot.cls}`} aria-hidden>{rot.glyph}</span>
            {rot.label}
          </span>
        </div>
        <h3 className="board-card-title">Week of {fmtWeek(w.week_start, w.week_end)}</h3>
        {preview && <p className="board-card-thesis">{preview}</p>}
        <div className="board-card-foot">
          <span className="board-ev-total">Macro report · market · DeFi · 7-day news</span>
        </div>
      </div>
    </li>
  );
}

export default function WeeklyView({ weeklies = [] }) {
  const [idx, setIdx] = useState(weeklies.length ? 0 : null);
  const { searchProp, clearSearch } = useHeaderSearch();

  const selected = idx != null ? (weeklies[idx] || null) : null;
  const panelOpen = !!selected || !!searchProp;

  const handleClose = () => {
    if (searchProp) clearSearch();
    else setIdx(null);
  };

  return (
    <div className="feed-grid">
      <div className="feed-left">
        <div className="feed-scroll">
          <div className="view-head">
            <div className="view-head-titles">
              <h1 className="view-title">Weekly Macro</h1>
              <p className="view-sub">Monday market + DeFi + 7-day narrative reports · {weeklies.length} editions</p>
            </div>
          </div>

          {weeklies.length === 0 ? (
            <div className="feed-empty">
              <div className="feed-empty-glyph" aria-hidden>≋</div>
              <p>No weekly reports yet.<br />The first runs Monday 07:30 UTC.</p>
            </div>
          ) : (
            <ul className="board-list">
              {weeklies.map((w, i) => (
                <WeeklyCard
                  key={w.week_start}
                  w={w}
                  selected={idx === i && !searchProp}
                  onSelect={() => { setIdx(i); clearSearch(); }}
                />
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className={`feed-right${panelOpen ? " panel-open" : ""}`}>
        <RightPanel
          weekly={searchProp ? null : selected}
          search={searchProp}
          onClose={handleClose}
        />
      </div>
    </div>
  );
}
