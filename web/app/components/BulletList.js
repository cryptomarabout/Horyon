"use client";

import { useState } from "react";
import DetailsButton from "./DetailsButton";

export default function BulletList({ bullets }) {
  const [openSet, setOpenSet] = useState(() => new Set());

  function toggle(i) {
    setOpenSet((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });
  }

  return (
    <ol className="bullets">
      {bullets.map((b, i) => {
        const expanded = openSet.has(i);
        return (
          <li key={i} className={`bullet${b.hack ? " hack" : ""}${expanded ? " expanded" : ""}`}>
            <button
              className="bullet-row"
              onClick={() => toggle(i)}
              aria-expanded={expanded}
            >
              <span className="bullet-num">{String(i + 1).padStart(2, "0")}</span>
              <span className="bullet-title">
                {b.hack ? "🚨 " : ""}
                {b.title || "Update"}
              </span>
              <span className="bullet-chevron" aria-hidden>
                {expanded ? "−" : "+"}
              </span>
              {b.link && (
                <a
                  className="bullet-src"
                  href={b.link}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  title="Source"
                >
                  ↗
                </a>
              )}
            </button>

            {expanded && (
              <div className="bullet-expand">
                {b.body && <p className="bullet-text">{b.body}</p>}
                <DetailsButton title={b.title} body={b.body} />
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
