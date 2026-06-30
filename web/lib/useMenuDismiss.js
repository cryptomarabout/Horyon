import { useEffect } from "react";

/**
 * Dismiss an open, anchored popover/menu on the interactions that should close
 * one: the Escape key, and any scroll or viewport resize (which would otherwise
 * leave the menu floating off its trigger's bounding rect).
 *
 * Shared by the portaled masthead menus (`FilterMenu`, `MainNav`'s page
 * selector) so the listener wiring lives in one place. No-op while closed.
 *
 * @param {boolean}    open  whether the menu is currently open
 * @param {() => void} close handler to dismiss the menu
 */
export default function useMenuDismiss(open, close) {
  useEffect(() => {
    if (!open) return;
    function onKey(e) { if (e.key === "Escape") close(); }
    function dismiss() { close(); }
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  // `close` is a stable setstate updater at both call sites.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}
