import { useState, useRef, useCallback } from "react";

// Open/close + trigger-anchored positioning for portal dropdowns (the masthead
// page select, the source filter). `computePos(rect)` is given the trigger's
// bounding rect and returns the inline-style object for the portaled menu, or
// `null` for a CSS-positioned bottom sheet. Pair with <MenuPortal> and a
// dismiss hook (useMenuDismiss / useMobilePanelBack) at the call site.
export default function useAnchoredMenu(computePos) {
  const [open, setOpen] = useState(false);
  const [pos, setPos]   = useState(null);
  const triggerRef      = useRef(null);

  const close = useCallback(() => setOpen(false), []);

  const openMenu = useCallback(() => {
    const r = triggerRef.current?.getBoundingClientRect();
    setPos(r ? computePos(r) : null);
    setOpen(true);
  }, [computePos]);

  // Convenience for the trigger button's onClick.
  const toggle = useCallback(() => (open ? close() : openMenu()), [open, close, openMenu]);

  return { open, pos, triggerRef, openMenu, close, toggle };
}
