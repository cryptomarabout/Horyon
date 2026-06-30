// Feed sort control — Top (importance) ⇄ Latest (recency). Pure presentational;
// the feed owns the `sortBy` state.
const SORT_OPTS = [
  { key: "importance", label: "Top" },
  { key: "recent",     label: "Latest" },
];

export default function SortToggle({ value, onChange }) {
  return (
    <div className="sort-toggle" role="group" aria-label="Sort signals" onClick={e => e.stopPropagation()}>
      {SORT_OPTS.map(o => (
        <button
          key={o.key}
          type="button"
          className={`sort-toggle-btn${value === o.key ? " is-active" : ""}`}
          aria-pressed={value === o.key}
          onClick={() => onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
