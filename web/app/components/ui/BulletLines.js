import PanelSection from "./PanelSection";

// Shared dotted bullet list (`.pw-lines` / `.pw-item` / `.pw-dot`) for plain
// string items — podcast key claims/predictions, narrative "watch next", etc.
// Pass `label` to wrap it in a titled PanelSection; omit it for a bare list.
export default function BulletLines({ label, count, items }) {
  if (!items?.length) return null;
  const list = (
    <div className="pw-lines">
      {items.map((t, i) => (
        <div key={i} className="pw-item">
          <span className="pw-dot" aria-hidden="true" />
          <span>{t}</span>
        </div>
      ))}
    </div>
  );
  return label ? <PanelSection label={label} count={count}>{list}</PanelSection> : list;
}
