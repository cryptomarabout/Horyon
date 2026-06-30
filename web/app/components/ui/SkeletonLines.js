export default function SkeletonLines({ widths = [100, 92, 85, 60], prefix = "panel-ai" }) {
  return (
    <div className={`${prefix}-skeleton`}>
      {widths.map((w, i) => (
        <div key={i} className={`sk-bone ${prefix}-bone`} style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}
