import { METRICS } from "../../../lib/entityGraph";
import { SearchIcon } from "../icons";
import TypeChips from "./TypeChips";

// Atlas toolbar: layout switcher (Board ⇄ Network), the type legend/filter, and —
// only where links drive the picture (Network) — the edge-metric + link-strength
// selectors, plus entity search. Pure presentational; all state lives in the root.
export default function MapToolbar({
  view, setView, metric, setMetric, level, setLevel, query, setQuery,
  active, counts, toggleType, allTypes,
}) {
  return (
    <div className="map-toolbar">
      <div className="map-toolbar-left">
        {/* Layout switcher — Index (default screener) ⇄ Board ⇄ Network */}
        <div className="eg-view" role="group" aria-label="Map layout">
          <button
            type="button"
            className={`eg-view-btn${view === "table" ? " is-active" : ""}`}
            aria-pressed={view === "table"}
            title="Ranked index of every entity — coverage, co-mention centrality & TVL"
            onClick={() => setView("table")}
          >
            Index
          </button>
          <button
            type="button"
            className={`eg-view-btn${view === "board" ? " is-active" : ""}`}
            aria-pressed={view === "board"}
            title="Tidy clusters grouped by type"
            onClick={() => setView("board")}
          >
            Board
          </button>
          <button
            type="button"
            className={`eg-view-btn${view === "network" ? " is-active" : ""}`}
            aria-pressed={view === "network"}
            title="Force-directed graph of relationships"
            onClick={() => setView("network")}
          >
            Network
          </button>
        </div>
        {/* The type legend doubles as the filter for all three views (graph + the
            Index table both honour the same `active` map). */}
        <TypeChips active={active} counts={counts} onToggle={toggleType} onAll={allTypes} />
      </div>
      <div className="map-toolbar-right">
        {/* Edge metric / link-strength only matter where links drive the picture —
            the Network. The Board groups by type, so it just keeps legend + search. */}
        {view === "network" && (
          <>
            <div className="eg-metric" role="group" aria-label="Edge metric">
              {Object.entries(METRICS).map(([k, m]) => (
                <button
                  key={k}
                  type="button"
                  className={`eg-metric-btn${metric === k ? " is-active" : ""}`}
                  aria-pressed={metric === k}
                  title={m.hint}
                  onClick={() => setMetric(k)}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <div className="eg-weight" role="group" aria-label="Link strength">
              {METRICS[metric].names.map((nm, i) => (
                <button
                  key={i}
                  type="button"
                  className={`eg-weight-btn${level === i ? " is-active" : ""}`}
                  aria-pressed={level === i}
                  onClick={() => setLevel(i)}
                >
                  {nm}
                </button>
              ))}
            </div>
          </>
        )}
        <div className="map-search">
          <SearchIcon size={12} />
          <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Find entity…" aria-label="Search entities" spellCheck={false} />
          {query && <button className="map-search-clear" onClick={() => setQuery("")} aria-label="Clear">✕</button>}
        </div>
      </div>
    </div>
  );
}
