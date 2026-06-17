"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// ── Secondary switcher inside the Narratives section: Board · Map ───────────
// The header MainNav keeps the Daily · Narratives · Weekly triad; this local
// toggle switches between the text board (NarrativeView) and the force-graph map.
// Both routes start with /narratives, so MainNav's "Narratives" item stays active
// for either. Mirrors MainNav: Link + usePathname active state, gold underline.
const ITEMS = [
  { id: "board", label: "Board", href: "/narratives",     match: (p) => p === "/narratives" },
  { id: "map",   label: "Map",   href: "/narratives/map", match: (p) => p.startsWith("/narratives/map") },
];

export default function NarrativeViewToggle() {
  const path = usePathname() || "/narratives";
  return (
    <nav className="nv-toggle" aria-label="Narrative view">
      {ITEMS.map((it) => {
        const active = it.match(path);
        return (
          <Link
            key={it.id}
            href={it.href}
            prefetch={false}
            className={`nv-toggle-link${active ? " is-active" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            {it.label}
          </Link>
        );
      })}
    </nav>
  );
}
