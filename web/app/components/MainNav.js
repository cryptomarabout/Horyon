"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// ── Primary view switcher: Daily · Narratives · Weekly ──────────────────────
// Replaces the old left sidebar + dropdown menus. Each item is a real route so
// the views are bookmarkable and the browser back button works (like the daily).
const ITEMS = [
  { id: "daily",      label: "Daily",      href: "/",           match: (p) => p === "/" || p.startsWith("/d/") },
  { id: "narratives", label: "Narratives", href: "/narratives", match: (p) => p.startsWith("/narratives") },
  { id: "weekly",     label: "Weekly",     href: "/weekly",     match: (p) => p.startsWith("/weekly") },
];

export default function MainNav() {
  const path = usePathname() || "/";
  return (
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
  );
}
