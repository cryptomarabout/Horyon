import { TYPE_META, TYPES } from "../../../lib/entityGraph";

// Type filter chips (= the color legend). One chip per entity type present,
// plus an "All" reset; the leading dot keys each chip to its node color.
export default function TypeChips({ active, counts, onToggle, onAll }) {
  const allOn = TYPES.every((t) => active[t] || (counts[t] || 0) === 0);
  return (
    <div className="srcfilter mapfilter" role="group" aria-label="Filter by type">
      <button
        type="button"
        className={`srcfilter-chip srcfilter-all${allOn ? " is-active" : ""}`}
        aria-pressed={allOn}
        onClick={onAll}
      >
        All
      </button>
      {TYPES.filter((t) => (counts[t] || 0) > 0).map((t) => (
        <button
          key={t}
          type="button"
          className={`srcfilter-chip mapfilter-chip egfilter-chip--${t}${active[t] ? " is-active" : ""}`}
          aria-pressed={active[t]}
          onClick={() => onToggle(t)}
        >
          <span className={`mapfilter-dot egdot--${t}`} aria-hidden />
          <span>{TYPE_META[t].label}</span>
          <span className="srcfilter-n">{counts[t]}</span>
        </button>
      ))}
    </div>
  );
}
