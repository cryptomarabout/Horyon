"use client";

import { useEffect, useRef, useState } from "react";
import { fmtWeekRange } from "../../../lib/weekly";

// Archive navigation — a "← Prev / Next →" report stepper plus a dropdown to jump
// to any past report (week range as the label — no issue/volume numbering).
// weeklies are newest-first, so idx 0 = the current report.
export default function IssueNav({ weeklies, idx, onPick }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, []);

  const newer = idx > 0 ? idx - 1 : null;                     // more recent report
  const older = idx < weeklies.length - 1 ? idx + 1 : null;   // previous (older) report
  const cur = weeklies[idx];

  return (
    <div className="wr-issuenav" ref={ref}>
      <button
        type="button"
        className={`wr-issue-step${older != null ? "" : " is-disabled"}`}
        onClick={() => older != null && onPick(older)}
        aria-label="Previous report"
      >
        ← Prev
      </button>

      <button
        type="button"
        className="wr-issue-current"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((o) => !o)}
      >
        {fmtWeekRange(cur.week_start, cur.week_end)}
        <span className="caret" aria-hidden="true">▾</span>
      </button>

      <button
        type="button"
        className={`wr-issue-step${newer != null ? "" : " is-disabled"}`}
        onClick={() => newer != null && onPick(newer)}
        aria-label="Next report"
      >
        Next →
      </button>

      {open && (
        <div className="wr-issue-menu" role="listbox" aria-label="Past reports">
          {weeklies.map((w, i) => (
            <button
              key={w.week_start}
              type="button"
              role="option"
              aria-selected={i === idx}
              className={`wr-issue-opt${i === idx ? " is-active" : ""}`}
              onClick={() => { onPick(i); setOpen(false); }}
            >
              <span>{fmtWeekRange(w.week_start, w.week_end)}</span>
              {i === 0 && <span className="wr-issue-opt-latest">CURRENT</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
