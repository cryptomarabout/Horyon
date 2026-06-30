export default function EmptyState({ glyph = "◈", children, variant = "panel" }) {
  const b = variant === "feed" ? "feed-empty" : "panel-empty";
  return (
    <div className={b}>
      <div className={`${b}-glyph`} aria-hidden="true">{glyph}</div>
      {variant === "feed"
        ? <p>{children}</p>
        : <p className="panel-empty-label">{children}</p>
      }
    </div>
  );
}
