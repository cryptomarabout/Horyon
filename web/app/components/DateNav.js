"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useRef, useEffect, useMemo } from "react";

const WD_FULL = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
const MO_FULL = ["January","February","March","April","May","June",
                 "July","August","September","October","November","December"];
const MO_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const WD_H = ["M","T","W","T","F","S","S"];

function fmtDs(d) {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2,"0")}-${String(d.getUTCDate()).padStart(2,"0")}`;
}

function parseDateParts(ds) {
  const [y, m, day] = ds.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, day));
  return { wd: WD_FULL[dt.getUTCDay()], month: MO_FULL[m - 1], monShort: MO_SHORT[m - 1], day, year: y };
}

// All Mon-Sun weeks whose Monday falls in the given month (0-indexed).
function getWeeksInMonth(year, month) {
  const lastDay = new Date(Date.UTC(year, month + 1, 0));
  const firstDay = new Date(Date.UTC(year, month, 1));
  const dow = (firstDay.getUTCDay() + 6) % 7; // Mon=0
  const firstMon = new Date(Date.UTC(year, month, 1 + (dow === 0 ? 0 : 7 - dow)));
  // include the days before the first Monday (prev-month tail) by starting one week earlier if needed
  const start = new Date(firstDay);
  start.setUTCDate(start.getUTCDate() - dow);

  const weeks = [];
  let cur = new Date(start);
  while (cur <= lastDay) {
    const days = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(cur); d.setUTCDate(d.getUTCDate() + i);
      days.push({ n: d.getUTCDate(), inMonth: d.getUTCMonth() === month, ds: fmtDs(d) });
    }
    weeks.push(days);
    cur.setUTCDate(cur.getUTCDate() + 7);
  }
  return weeks;
}

// ── Calendar icon ───────────────────────────────────────────────────────────
function CalIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="3" width="12" height="11" rx="1.5" />
      <line x1="2" y1="6.5" x2="14" y2="6.5" />
      <line x1="5.5" y1="1.5" x2="5.5" y2="4" />
      <line x1="10.5" y1="1.5" x2="10.5" y2="4" />
    </svg>
  );
}

function Chevron({ dir }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      {dir === "left"
        ? <polyline points="10,3 5,8 10,13" />
        : <polyline points="6,3 11,8 6,13" />}
    </svg>
  );
}

// ── Calendar popover ─────────────────────────────────────────────────────────
function CalendarPopover({ digestMap, currentDate, today, onPick }) {
  const [y0, m0] = currentDate.split("-").map(Number);
  const [view, setView] = useState({ y: y0, m: m0 - 1 }); // month being shown
  const weeks = useMemo(() => getWeeksInMonth(view.y, view.m), [view]);

  const step = (delta) => setView(v => {
    const d = new Date(Date.UTC(v.y, v.m + delta, 1));
    return { y: d.getUTCFullYear(), m: d.getUTCMonth() };
  });

  return (
    <div className="datenav-cal" role="dialog" aria-label="Pick a date">
      <div className="datenav-cal-head">
        <button type="button" className="datenav-cal-nav" onClick={() => step(-1)} aria-label="Previous month">
          <Chevron dir="left" />
        </button>
        <span className="datenav-cal-title">{MO_FULL[view.m]} {view.y}</span>
        <button type="button" className="datenav-cal-nav" onClick={() => step(1)} aria-label="Next month">
          <Chevron dir="right" />
        </button>
      </div>
      <div className="datenav-cal-grid">
        {WD_H.map((h, i) => <span key={i} className="datenav-cal-gh">{h}</span>)}
        {weeks.flat().map(c => {
          const count = digestMap[c.ds];
          const isToday = c.ds === today;
          const isOn = c.ds === currentDate;
          const future = c.ds > today;
          if (count != null) {
            return (
              <Link
                key={c.ds}
                href={`/d/${c.ds}`}
                prefetch={false}
                onClick={onPick}
                className={`datenav-cal-cell has${isOn ? " on" : ""}${isToday ? " today" : ""}${c.inMonth ? "" : " dim"}`}
                title={`${count} signals`}
              >
                <span className="datenav-cal-n">{c.n}</span>
                <span className="datenav-cal-dot" aria-hidden />
              </Link>
            );
          }
          return (
            <span key={c.ds} className={`datenav-cal-cell${isToday ? " today" : ""}${c.inMonth ? "" : " dim"}${future ? " future" : ""}`}>
              <span className="datenav-cal-n">{c.n}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

// ── Date navigator: ‹ [weekday · date ▾] › ──────────────────────────────────
// Replaces the removed sidebar calendar. Chevrons step to the prev/next
// available digest day; the date label opens a month calendar to jump anywhere.
export default function DateNav({ items = [], currentDate }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // newest-first list of dates that have a digest
  const dates = useMemo(() => items.map(it => it.date), [items]);
  const digestMap = useMemo(() => {
    const m = {};
    items.forEach(it => { m[it.date] = it.bullets; });
    return m;
  }, [items]);
  const today = useMemo(() => fmtDs(new Date()), []);

  const idx = dates.indexOf(currentDate);
  const olderDate = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null; // ‹ back in time
  const newerDate = idx > 0 ? dates[idx - 1] : null;                            // › forward

  useEffect(() => {
    function onDown(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    function onKey(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, []);

  // Keyboard: [ / ] step prev/next digest day (when not typing)
  useEffect(() => {
    function onKey(e) {
      const inInput = e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable;
      if (inInput) return;
      if (e.key === "[" && olderDate) { e.preventDefault(); router.push(`/d/${olderDate}`); }
      if (e.key === "]" && newerDate) { e.preventDefault(); router.push(`/d/${newerDate}`); }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [olderDate, newerDate, router]);

  const dp = parseDateParts(currentDate);

  return (
    <div className="datenav" ref={ref}>
      <Link
        href={olderDate ? `/d/${olderDate}` : "#"}
        prefetch={false}
        className={`datenav-step${olderDate ? "" : " is-disabled"}`}
        aria-label="Previous digest"
        aria-disabled={!olderDate}
        onClick={e => { if (!olderDate) e.preventDefault(); }}
      >
        <Chevron dir="left" />
      </Link>

      <button
        type="button"
        className={`datenav-label${open ? " is-open" : ""}`}
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <span className="datenav-cal-icon" aria-hidden><CalIcon /></span>
        <span className="datenav-wd">{dp.wd}</span>
        <span className="datenav-sep" aria-hidden>·</span>
        <span className="datenav-full">{dp.monShort} {dp.day}, {dp.year}</span>
        <span className={`datenav-caret${open ? " up" : ""}`} aria-hidden>▾</span>
      </button>

      <Link
        href={newerDate ? `/d/${newerDate}` : "#"}
        prefetch={false}
        className={`datenav-step${newerDate ? "" : " is-disabled"}`}
        aria-label="Next digest"
        aria-disabled={!newerDate}
        onClick={e => { if (!newerDate) e.preventDefault(); }}
      >
        <Chevron dir="right" />
      </Link>

      {open && (
        <CalendarPopover
          digestMap={digestMap}
          currentDate={currentDate}
          today={today}
          onPick={() => setOpen(false)}
        />
      )}
    </div>
  );
}
