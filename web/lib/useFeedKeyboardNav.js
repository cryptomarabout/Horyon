import { useEffect, useRef } from "react";

// Keyboard navigation for the daily feed: ↓/j and ↑/k move the cursor, →/Enter
// opens the cursored row in the panel, ←/Esc steps back out, and "/" focuses the
// header search. Live state is mirrored into a ref so the keydown handler binds
// once instead of re-subscribing on every cursor move. Also scrolls the cursored
// row into view. The feed owns the state; this drives it via the passed setters.
export default function useFeedKeyboardNav({
  count, selected, cursor, panelOpen, setCursor, setSelected, setPanelOpen, listRef,
}) {
  const kbRef = useRef({ selected: null, cursor: null, panelOpen: false, count: 0 });
  useEffect(() => {
    kbRef.current = { selected, cursor, panelOpen, count };
  });

  useEffect(() => {
    function onKeyDown(e) {
      const inInput = e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable;
      if (e.key === "/") {
        if (inInput) return;
        e.preventDefault();
        document.dispatchEvent(new CustomEvent("horyon:focus-search"));
        return;
      }
      if (inInput) return;
      if (e.target.closest?.(".bullet")) return;

      const { selected: sel, cursor: cur, count: n } = kbRef.current;
      if (!n) return;

      switch (e.key) {
        case "ArrowDown": case "j": case "J":
          e.preventDefault();
          setCursor(c => c === null ? 0 : Math.min(c + 1, n - 1));
          break;
        case "ArrowUp": case "k": case "K":
          e.preventDefault();
          setCursor(c => c === null ? n - 1 : Math.max(c - 1, 0));
          break;
        case "ArrowRight": case "Enter":
          e.preventDefault();
          if (cur !== null) { setSelected(cur); setPanelOpen(true); }
          break;
        case "ArrowLeft": case "Escape":
          e.preventDefault();
          if (sel !== null) { setCursor(sel); setSelected(null); setPanelOpen(false); }
          else if (cur !== null) { setCursor(null); }
          break;
        default: break;
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [setCursor, setSelected, setPanelOpen]);

  useEffect(() => {
    if (cursor === null) return;
    const ul = listRef.current;
    if (!ul) return;
    ul.children[cursor]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [cursor, listRef]);
}
