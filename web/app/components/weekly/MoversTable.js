// Movers league table — one compact block, gainers and losers on the SAME row.
// Each rank is a single line: rank · top gainer (left) · top loser (right). A faint
// proportional fill behind each cell conveys magnitude without adding height.
import { fmtPct } from "../../../lib/format";

const TOP_N = 5;

function Cell({ t, dir, maxAbs }) {
  if (!t) return <span className="wr-mv-cell is-empty" />;
  const w = maxAbs ? Math.max(8, (Math.abs(t.pct) / maxAbs) * 100) : 0;
  return (
    <div className={`wr-mv-cell ${dir}`}>
      <i className={`wr-mv-fill ${dir}`} style={{ width: `${w}%` }} aria-hidden="true" />
      <span className="wr-mv-sym">{t.sym}</span>
      <span className={`wr-mv-pct ${dir}`}>{fmtPct(t.pct)}</span>
    </div>
  );
}

export default function MoversTable({ gainers = [], losers = [] }) {
  if (!gainers.length && !losers.length) return null;
  const g = gainers.slice(0, TOP_N);
  const l = losers.slice(0, TOP_N);
  const rows = Math.max(g.length, l.length);
  const maxAbs = Math.max(
    1,
    ...g.map((t) => Math.abs(t.pct)),
    ...l.map((t) => Math.abs(t.pct))
  );
  return (
    <div className="wr-movers">
      <div className="wr-mv-head">
        <span />
        <span className="gain">Gainers · 7d</span>
        <span className="loss">Losers · 7d</span>
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div className="wr-mv-row" key={i}>
          <span className="wr-mv-rank">{i + 1}</span>
          <Cell t={g[i]} dir="up" maxAbs={maxAbs} />
          <Cell t={l[i]} dir="dn" maxAbs={maxAbs} />
        </div>
      ))}
    </div>
  );
}
