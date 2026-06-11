"use client";

import { useState, useRef, useEffect } from "react";

// Gavel icon — single consistent stroke weight, matches the icon set.
function GavelIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M14 13l-7.5 7.5a2.1 2.1 0 0 1-3-3L11 10" />
      <path d="M9.5 7.5l7 7" />
      <path d="M12.5 4.5l7 7" />
      <path d="M16 3l5 5" />
      <line x1="14" y1="21" x2="22" y2="21" />
    </svg>
  );
}

function timeLeft(iso) {
  if (!iso) return null;
  const diff = new Date(iso) - Date.now();
  if (diff <= 0) return null;
  const h = Math.floor(diff / 3_600_000);
  if (h < 1) return `${Math.ceil(diff / 60_000)}m`;
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

function urgencyCls(iso) {
  if (!iso) return "";
  const diff = new Date(iso) - Date.now();
  if (diff <= 0) return "";
  if (diff < 86_400_000) return "gov-dd-time--hot";
  if (diff < 259_200_000) return "gov-dd-time--warn";
  return "";
}

// Header governance access — a compact icon button that opens a popover of
// active DAO proposals. Keeps governance out of the primary nav (it's bursty,
// secondary) but one click away from every view.
export default function GovernanceMenu({ governance = [] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, []);

  if (!governance.length) return null;

  return (
    <div className="gov-menu" ref={ref}>
      <button
        type="button"
        className={`gov-menu-btn${open ? " is-open" : ""}`}
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label="DAO governance proposals"
        title="DAO governance proposals"
      >
        <GavelIcon />
        <span className="gov-menu-count">{governance.length}</span>
      </button>

      {open && (
        <div className="nav-dropdown gov-dropdown" role="menu">
          <div className="gov-dd-head">Active DAO Proposals</div>
          <div className="nav-dd-list">
            {governance.map(p => {
              const left = timeLeft(p.end_ts);
              return (
                <a
                  key={p.proposal_id}
                  className="nav-dd-item"
                  target="_blank"
                  rel="noreferrer"
                  href={`https://snapshot.org/#/${p.space_id}/proposal/${p.proposal_id}`}
                  onClick={() => setOpen(false)}
                  role="menuitem"
                >
                  <img className="nav-dd-logo" src={`https://cdn.stamp.fyi/space/${p.space_id}?s=48`} alt=""
                    onError={e => { e.currentTarget.style.visibility = "hidden"; }} />
                  <span className="nav-dd-body">
                    <span className="nav-dd-title">{p.title}</span>
                    <span className="nav-dd-sub">{p.space_name}</span>
                  </span>
                  {left && <span className={`nav-dd-time ${urgencyCls(p.end_ts)}`}>{left}</span>}
                </a>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
