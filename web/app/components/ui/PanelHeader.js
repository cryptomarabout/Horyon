// Shared right-panel header: the `.panel-title-row` + close button that every
// sub-panel (bullet, search, narrative, podcast, weekly, map node/edge, related
// overlay) renders identically. `children` is the left-hand title content;
// `below` is the optional sub-header row (eyebrow, meta-row, tabs, src-link).
export default function PanelHeader({ children, onClose, closeLabel = "Close panel", tabbed = false, below }) {
  return (
    <div className={`panel-header${tabbed ? " panel-header--tabbed" : ""}`}>
      <div className="panel-title-row">
        {children}
        <button className="panel-close" onClick={onClose} aria-label={closeLabel}>✕</button>
      </div>
      {below}
    </div>
  );
}
