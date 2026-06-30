"use client";

import InfoTip from "./ui/InfoTip";

// Daily-feed header legend for the bullet left-edge colours. Interaction is the
// shared <InfoTip> primitive; this just supplies the swatch content + the
// `design-legend*` styling.
const SWATCHES = [
  ["#F87171", "Security (hack, breach)"],
  ["#D4AF37", "Governance (proposal, vote)"],
  ["#34D399", "Launch (deploy, integrate)"],
  ["rgba(255,255,255,0.12)", "Neutral / other"],
];

export default function DesignLegend() {
  return (
    <InfoTip
      label="Left-edge color legend"
      iconSize={10}
      btnClassName="design-legend"
      popClassName="design-legend-tooltip"
    >
      <div style={{ fontSize: "10px", lineHeight: "1.4", color: "var(--text-2)" }}>
        <div style={{ marginBottom: "6px", fontWeight: 600, color: "var(--text)" }}>
          Left-edge colors:
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
          {SWATCHES.map(([color, label]) => (
            <div key={label} style={{ display: "flex", gap: "6px", alignItems: "center" }}>
              <span style={{ width: "3px", height: "8px", background: color, borderRadius: "1px", flexShrink: 0 }} />
              <span>{label}</span>
            </div>
          ))}
        </div>
      </div>
    </InfoTip>
  );
}
