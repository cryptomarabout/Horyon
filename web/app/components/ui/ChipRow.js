import PanelSection from "./PanelSection";

// Shared chip row (`.pod-chips`) for tag-style lists. Plain string items render
// as `.pod-chip` spans; pass `renderChip(item, i)` for custom chips (e.g. an
// entity chip with an avatar). `label` wraps it in a titled PanelSection.
export default function ChipRow({ label, items, chipClass = "pod-chip", renderChip }) {
  if (!items?.length) return null;
  const chips = (
    <div className="pod-chips">
      {items.map((t, i) =>
        renderChip ? renderChip(t, i) : <span key={i} className={chipClass}>{t}</span>
      )}
    </div>
  );
  return label ? <PanelSection label={label}>{chips}</PanelSection> : chips;
}
