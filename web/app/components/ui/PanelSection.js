export default function PanelSection({ label, count, children, className }) {
  return (
    <div className={className}>
      <div className="panel-section-label">
        {count != null ? `${label} · ${count}` : label}
      </div>
      {children}
    </div>
  );
}
