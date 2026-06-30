import { fmtLen, VARIANT_LABELS } from "../../../lib/audio";

// Segmented length switcher (Flash · Briefing · Deep Dive) above the player.
export default function AudioVariants({ list, variant, onSwitch }) {
  return (
    <div className="audio-variants-wrap">
      <div className="audio-variants" role="tablist" aria-label="Briefing length">
        {list.map((v) => (
          <button
            key={v.variant}
            type="button"
            role="tab"
            aria-selected={v.variant === variant}
            className={`audio-variant${v.variant === variant ? " active" : ""}`}
            onClick={() => onSwitch(v.variant)}
            title={`${VARIANT_LABELS[v.variant] || v.variant}${
              v.duration_sec ? ` · ${fmtLen(v.duration_sec)}` : ""
            }`}
          >
            <span className="audio-variant-name">{VARIANT_LABELS[v.variant] || v.variant}</span>
            {v.duration_sec ? <span className="audio-variant-len">{fmtLen(v.duration_sec)}</span> : null}
          </button>
        ))}
      </div>
    </div>
  );
}
