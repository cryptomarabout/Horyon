"use client";

import { useCallback } from "react";
import SourceFilter from "./SourceFilter";
import { FilterIcon } from "./icons";
import MenuPortal from "./ui/MenuPortal";
import useAnchoredMenu from "../../lib/useAnchoredMenu";
import useMobilePanelBack from "../../lib/useMobilePanelBack";
import useMenuDismiss from "../../lib/useMenuDismiss";

// Source filtering, demoted from an always-on chip row to a single masthead
// button → anchored dropdown (desktop) / bottom sheet (≤900px). A dot on the
// trigger signals an active (non-default) filter so the state is never hidden.
// The toggles are the shared `SourceFilter` (single source of truth); `onAll`
// doubles as the in-sheet Reset. Anchoring/portaling is shared via
// useAnchoredMenu + MenuPortal.
export default function FilterMenu({ active, counts = {}, onToggle, onAll }) {
  const allOn = ["news", "tweets", "podcasts"].every((k) => active[k]);

  // Desktop (≥901px) anchors a dropdown under the trigger; narrower screens get a
  // CSS-positioned bottom sheet (computePos → null).
  const computePos = useCallback((r) => {
    const desktop =
      typeof window !== "undefined" && window.matchMedia("(min-width: 901px)").matches;
    return desktop ? { top: r.bottom + 8, right: window.innerWidth - r.right } : null;
  }, []);

  const { open, pos, triggerRef, close, toggle } = useAnchoredMenu(computePos);

  useMobilePanelBack(open, close);
  // Esc closes; a scroll/resize closes the desktop dropdown so it never drifts off its anchor.
  useMenuDismiss(open, close);

  return (
    <div className="filter-menu" onClick={(e) => e.stopPropagation()}>
      <button
        ref={triggerRef}
        type="button"
        className={`filter-btn${open ? " is-open" : ""}${!allOn ? " is-filtered" : ""}`}
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Filter by source"
      >
        <span className="filter-btn-ic" aria-hidden><FilterIcon size={13} /></span>
        <span className="filter-btn-label">Filter</span>
        {!allOn && <span className="filter-btn-dot" aria-hidden />}
      </button>

      <MenuPortal
        open={open}
        onClose={close}
        backdropClass="filter-backdrop"
        className={`filter-sheet${pos ? " is-anchored" : ""}`}
        style={pos ? { top: pos.top, right: pos.right } : undefined}
        role="dialog"
        ariaLabel="Filter by source"
      >
        <div className="filter-sheet-head">
          <span className="filter-sheet-title">Sources</span>
          <button
            type="button"
            className="filter-sheet-close"
            onClick={close}
            aria-label="Close filter"
          >
            ✕
          </button>
        </div>
        <SourceFilter active={active} counts={counts} onToggle={onToggle} onAll={onAll} />
      </MenuPortal>
    </div>
  );
}
