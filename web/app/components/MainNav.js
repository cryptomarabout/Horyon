"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback } from "react";
import { CaretIcon } from "./icons";
import MenuPortal from "./ui/MenuPortal";
import useAnchoredMenu from "../../lib/useAnchoredMenu";
import useMenuDismiss from "../../lib/useMenuDismiss";

// ── Primary view switcher: Daily Briefs · Weekly Briefs · Themes · Atlas ─────
// Replaces the old left sidebar + dropdown menus. Each item is a real route so
// the views are bookmarkable and the browser back button works (routes are
// unchanged — only the labels were rebranded).
// Desktop (≥541px): inline tab strip in the header (.main-nav).
// Mobile (≤540px): a compact, brand-styled dropdown that lives INSIDE the
// masthead (.pageselect) — no longer a full-width <select> on its own row.
const ITEMS = [
  { id: "daily",      label: "Daily Briefs",  href: "/",           match: (p) => p === "/" || p.startsWith("/d/") },
  { id: "weekly",     label: "Weekly Briefs", href: "/weekly",     match: (p) => p.startsWith("/weekly") },
  { id: "narratives", label: "Research",       href: "/narratives", match: (p) => p === "/narratives" },
  { id: "map",        label: "Atlas",         href: "/map",        match: (p) => p.startsWith("/map") },
];

export default function MainNav() {
  const path = usePathname() || "/";
  const current = ITEMS.find((it) => it.match(path)) || ITEMS[0];

  return (
    <>
      {/* Desktop: inline tab strip */}
      <nav className="main-nav" aria-label="Primary views">
        {ITEMS.map((it) => {
          const active = it.match(path);
          return (
            <Link
              key={it.id}
              href={it.href}
              prefetch={false}
              className={`main-nav-link${active ? " is-active" : ""}`}
              aria-current={active ? "page" : undefined}
            >
              {it.label}
            </Link>
          );
        })}
      </nav>

      {/* Mobile: designed dropdown in the masthead */}
      <PageSelect current={current} path={path} />
    </>
  );
}

// Compact masthead dropdown for phones. The menu is portaled to <body> (the
// header carries a backdrop-filter, which would otherwise trap a fixed-position
// child) and anchored to the trigger via its bounding rect — same pattern as
// FilterMenu.
function PageSelect({ current, path }) {
  const computePos = useCallback((r) => ({ top: r.bottom + 8, left: r.left, width: r.width }), []);
  const { open, pos, triggerRef, close, toggle } = useAnchoredMenu(computePos);

  // Esc closes; a scroll/resize closes the menu so it never drifts off its anchor.
  useMenuDismiss(open, close);

  return (
    <div className="pageselect" onClick={(e) => e.stopPropagation()}>
      <button
        ref={triggerRef}
        type="button"
        className={`pageselect-btn${open ? " is-open" : ""}`}
        onClick={toggle}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Select view"
      >
        <span className="pageselect-label">{current.label}</span>
        <span className={`pageselect-caret${open ? " up" : ""}`} aria-hidden>
          <CaretIcon />
        </span>
      </button>

      <MenuPortal
        open={open}
        onClose={close}
        backdropClass="pageselect-backdrop"
        className="pageselect-menu"
        role="menu"
        style={pos ? { top: pos.top, left: pos.left, minWidth: Math.max(pos.width, 188) } : undefined}
      >
        {ITEMS.map((it) => {
          const active = it.match(path);
          return (
            <Link
              key={it.id}
              href={it.href}
              prefetch={false}
              role="menuitem"
              className={`pageselect-item${active ? " is-active" : ""}`}
              aria-current={active ? "page" : undefined}
              onClick={close}
            >
              <span>{it.label}</span>
              {active && <span className="pageselect-tick" aria-hidden>✓</span>}
            </Link>
          );
        })}
      </MenuPortal>
    </div>
  );
}
