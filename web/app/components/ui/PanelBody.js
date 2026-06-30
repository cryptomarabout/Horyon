// Shared scrollable body wrapper for right-panel content: the
// `.panel-scroll > .panel-body` pair every sub-panel repeats. `className`/`style`
// extend the inner body for the few callers that need padding overrides.
export default function PanelBody({ children, className, style }) {
  return (
    <div className="panel-scroll">
      <div className={className ? `panel-body ${className}` : "panel-body"} style={style}>
        {children}
      </div>
    </div>
  );
}
