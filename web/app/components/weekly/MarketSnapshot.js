// Market Snapshot — a slim single-row data ribbon that signals "data firm".
// Renders whatever cells lib/weekly.snapshotCells produced (column-backed or
// parsed from the market prose, plus DeFi TVL from the DB) as one compact line:
// a small label over a value + inline delta. Cells with no value are dropped
// upstream, so the strip self-sizes and never shows blanks; it scrolls
// horizontally on narrow screens to stay a single line.

function delta(cell) {
  if (cell.delta == null || !isFinite(cell.delta)) return null;
  const up = cell.delta > 0;
  const unit = cell.deltaUnit === "pp" ? "pp" : "%";
  const cls = cell.goodWhenUp == null ? "" : (up === Boolean(cell.goodWhenUp) ? "up" : "dn");
  const suffix = cell.deltaUnit && cell.deltaUnit !== "pp" ? ` ${cell.deltaUnit}` : "";
  return (
    <span className={`wr-stat-delta ${cls}`}>
      {up ? "+" : ""}{cell.delta.toFixed(1)}{unit}{suffix}
    </span>
  );
}

export default function MarketSnapshot({ cells = [] }) {
  if (!cells.length) return null;
  return (
    <section className="wr-snapshot" aria-label="Market snapshot">
      <div className="wr-snapshot-label">Market Snapshot</div>
      <div className="wr-stats">
        {cells.map((c) => {
          const valCls = c.isDeltaValue ? (c.value.startsWith("-") ? "dn" : "up") : "";
          return (
            <div className="wr-stat" key={c.key}>
              <div className="wr-stat-label">{c.label}</div>
              <div className="wr-stat-line">
                <span className={`wr-stat-value ${valCls}`}>{c.value}</span>
                {delta(c)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
