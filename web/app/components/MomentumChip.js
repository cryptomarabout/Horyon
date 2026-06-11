import { momentumArrow, deltaLabel, stateMeta } from "../../lib/narratives";

// Momentum indicator: state glyph + directional arrow + delta badge.
// Compact (board rows) or expanded (dossier header, with ratio).
export default function MomentumChip({ rho, delta, state, expanded = false }) {
  const { arrow, dir } = momentumArrow(rho);
  const sm = stateMeta(state);
  return (
    <span className={`momentum momentum--${dir} momentum--${sm.cls}`}>
      <span className="momentum-arrow" aria-hidden="true">{arrow}</span>
      <span className="momentum-delta">{deltaLabel(delta, state)}</span>
      {expanded && rho != null && (
        <span className="momentum-rho" title="momentum ratio ρ = (recent+1)/(baseline+1)">
          ρ&nbsp;{rho.toFixed(2)}
        </span>
      )}
    </span>
  );
}
