import { useEffect, useRef } from "react";

/**
 * Mobile back-gesture → close the full-screen right panel instead of leaving
 * the page.
 *
 * On a ≤900px viewport (the breakpoint where `.feed-right` becomes a
 * full-screen overlay), opening the panel pushes ONE dummy history entry; the
 * Back gesture/button then pops it and we call `onClose` rather than navigating
 * to the previous page. A programmatic close (✕, click-away, Esc, selection or
 * filter change) consumes that dummy entry via `history.back()`, so the history
 * stack stays clean either way.
 *
 * No-op on desktop (the panel is a static column there — Back should navigate
 * normally) and during SSR.
 *
 * @param {boolean}  open     whether the right panel is currently open
 * @param {() => void} onClose close handler for the panel
 */
export default function useMobilePanelBack(open, onClose) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const pushedRef = useRef(false); // true while OUR dummy history entry is live

  // Register the popstate listener once.
  useEffect(() => {
    const onPop = () => {
      if (!pushedRef.current) return; // not our dummy entry → let it navigate
      pushedRef.current = false;
      onCloseRef.current?.();
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Sync the dummy history entry with the panel's open state.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const isMobile = window.matchMedia("(max-width: 900px)").matches;

    if (open && isMobile && !pushedRef.current) {
      pushedRef.current = true;
      // Preserve Next.js's router state; just tag this entry as ours.
      window.history.pushState({ ...window.history.state, __horyonPanel: true }, "");
    } else if (!open && pushedRef.current) {
      // Closed by some other means → pop our dummy entry. popstate fires but
      // the flag is already cleared, so onClose won't double-fire.
      pushedRef.current = false;
      window.history.back();
    }
  }, [open]);
}
