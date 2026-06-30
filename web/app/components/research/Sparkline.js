"use client";
import { useState } from "react";

// Pure-SVG activity sparkline with hover tooltips. Bars scale to series max;
// the final bar (most recent bin) is highlighted. Tooltip shows count + date label.
export default function Sparkline({ data = [], labels = [], vbWidth = 240, height = 32, gap = 3, className }) {
  const [hovered, setHovered] = useState(null); // index | null

  if (!data.length) return null;
  const max = Math.max(1, ...data);
  const n = data.length;
  const bw = Math.max(1, (vbWidth - gap * (n - 1)) / n);

  return (
    <div className={`rsrch-spark-wrap${className ? " " + className : ""}`}>
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${vbWidth} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Development cadence over the coverage window"
        onMouseLeave={() => setHovered(null)}
      >
        {data.map((v, i) => {
          const h = Math.max(2, (v / max) * (height - 2));
          return (
            <rect
              key={i}
              x={i * (bw + gap)}
              y={height - h}
              width={bw}
              height={h}
              rx="1"
              className={[
                "rsrch-spark-bar",
                i === n - 1 ? "is-now" : "",
                v === 0 ? "is-empty" : "",
                hovered === i ? "is-hovered" : "",
              ].filter(Boolean).join(" ")}
              onMouseEnter={() => setHovered(i)}
            />
          );
        })}
      </svg>
      {hovered !== null && (
        <div
          className="rsrch-spark-tip"
          style={{ left: `${((hovered + 0.5) / n) * 100}%` }}
          aria-hidden
        >
          {labels[hovered] && <span className="rsrch-spark-tip-date">{labels[hovered]}</span>}
          <span className="rsrch-spark-tip-val">{data[hovered]}</span>
        </div>
      )}
    </div>
  );
}
