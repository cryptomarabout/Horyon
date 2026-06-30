"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { InfoIcon } from "../icons";

// ── Generic info affordance ──────────────────────────────────────────────────
// An icon button that reveals a tooltip / popover card. Opens on hover + focus,
// toggles on click + touch, and dismisses on Escape, outside-click, or
// pointer-leave (after a short grace delay so the cursor can travel from the
// trigger into the card). Anchored inline: the card is position:absolute inside
// the `.infotip` wrapper, so the caller's `popClassName` owns size + which edge
// it aligns to.
//
// Consolidates the Atlas Index methodology popover (`IndexInfo`) and the daily-
// feed design legend (`DesignLegend`), which were the same widget written twice.
export default function InfoTip({
  children,
  label = "More information",
  title,
  icon,
  iconSize = 14,
  wrapClassName = "",
  btnClassName = "infotip-btn",
  popClassName = "infotip-pop",
  role = "tooltip",
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const hideTimer = useRef(null);

  const show = useCallback(() => { clearTimeout(hideTimer.current); setOpen(true); }, []);
  const hide = useCallback(() => {
    hideTimer.current = setTimeout(() => setOpen(false), 120);
  }, []);
  const hideNow = useCallback(() => { clearTimeout(hideTimer.current); setOpen(false); }, []);

  // Escape + outside-click dismiss, wired only while open.
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) hideNow();
    };
    const onKey = (e) => { if (e.key === "Escape") hideNow(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, hideNow]);

  // Drop any pending close timer on unmount.
  useEffect(() => () => clearTimeout(hideTimer.current), []);

  return (
    <div
      className={`infotip${wrapClassName ? ` ${wrapClassName}` : ""}`}
      ref={wrapRef}
      onMouseEnter={show}
      onMouseLeave={hide}
    >
      <button
        type="button"
        className={`${btnClassName}${open ? " is-active" : ""}`}
        aria-label={label}
        aria-expanded={open}
        title={title}
        onFocus={show}
        onBlur={hide}
        onClick={() => (open ? hideNow() : show())}
        onTouchStart={(e) => { e.preventDefault(); setOpen((o) => !o); }}
      >
        {icon || <InfoIcon size={iconSize} />}
      </button>
      {open && (
        <div className={popClassName} role={role}>
          {children}
        </div>
      )}
    </div>
  );
}
