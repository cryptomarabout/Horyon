"use client";

import { useEffect, useState } from "react";

// In-page Contents rail — replaces the section tabs. Lives in the sticky
// feed-head; numbered links smooth-scroll to each section and a scroll-spy
// (IntersectionObserver) highlights the section currently in view. This is the
// publication pattern: one flowing document you navigate, not filtered tabs.
export default function ReportContents({ sections }) {
  const [active, setActive] = useState(sections[0]?.id || null);

  useEffect(() => {
    const els = sections
      .map((s) => document.getElementById(`wr-${s.id}`))
      .filter(Boolean);
    if (!els.length) return;
    const obs = new IntersectionObserver(
      (entries) => {
        const vis = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (vis[0]) setActive(vis[0].target.id.replace(/^wr-/, ""));
      },
      { rootMargin: "-90px 0px -65% 0px", threshold: 0 }
    );
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [sections]);

  const go = (id) => {
    const el = document.getElementById(`wr-${id}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    setActive(id);
  };

  if (sections.length < 2) return null;

  return (
    <nav className="wr-contents" aria-label="Contents">
      <span className="wr-contents-label">Contents</span>
      {sections.map((s) => (
        <button
          key={s.id}
          type="button"
          className={`wr-toc${active === s.id ? " active" : ""}`}
          aria-current={active === s.id ? "true" : undefined}
          onClick={() => go(s.id)}
        >
          <span className="wr-toc-num">{s.num}</span>
          {s.nav}
        </button>
      ))}
    </nav>
  );
}
