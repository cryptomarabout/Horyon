import { fmt, VARIANT_LABELS } from "../../../lib/audio";
import { PlayIcon, PauseIcon, CaretIcon } from "../icons";

// Collapsed "Listen" strip — a play button + meta row + inline progress bar.
// The whole meta row is one tap target that expands the full player.
export default function AudioCollapsed({ playing, variant, cur, dur, onToggle, onExpand }) {
  const pct = dur > 0 ? Math.min(100, (cur / dur) * 100) : 0;
  return (
    <div className="audio-collapsed">
      <div className="audio-collapsed-row">
        <button
          className="audio-play"
          onClick={onToggle}
          aria-label={playing ? "Pause briefing" : "Play daily briefing"}
        >
          {playing ? <PauseIcon size={15} /> : <PlayIcon size={14} />}
        </button>
        <button
          type="button"
          className="audio-collapsed-meta"
          onClick={onExpand}
          aria-expanded={false}
          aria-label="Expand briefing player"
        >
          <span className="audio-collapsed-title">
            Listen<span className="audio-collapsed-sep"> · </span>{VARIANT_LABELS[variant] || "Briefing"}
          </span>
          <span className="audio-collapsed-time">{cur > 0 ? `${fmt(cur)} / ` : ""}{fmt(dur)}</span>
          <span className="audio-collapsed-caret" aria-hidden><CaretIcon size={12} /></span>
        </button>
      </div>
      {dur > 0 && (
        <div className="audio-collapsed-prog" aria-hidden>
          <div className="audio-collapsed-prog-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}
