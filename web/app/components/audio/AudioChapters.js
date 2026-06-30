import { fmt } from "../../../lib/audio";

// Expanded chapter list — each row jumps the player to its start; an optional
// ↗ opens the matching news story in the RightPanel (when onOpenStory is given).
export default function AudioChapters({ chaps, activeChap, onJump, onOpenStory }) {
  return (
    <ol className="audio-chapters">
      {chaps.map((c, i) => (
        <li key={i} className="audio-chapter-row">
          <button
            className={`audio-chapter${i === activeChap ? " active" : ""}`}
            onClick={() => onJump(c.start)}
          >
            <span className="audio-chapter-time">{fmt(c.start)}</span>
            <span className="audio-chapter-body">
              <span className="audio-chapter-title">{c.title}</span>
              {c.entities?.length > 0 && (
                <span className="audio-chapter-entities">
                  {c.entities.map((e, j) => (
                    <span key={j} className="audio-chapter-entity">{e}</span>
                  ))}
                </span>
              )}
            </span>
          </button>
          {onOpenStory && c.bullet_title && (
            <button
              className="audio-chapter-open"
              onClick={ev => { ev.stopPropagation(); onOpenStory(c.bullet_title); }}
              title="Open full story"
              aria-label={`Open full story: ${c.title}`}
            >
              ↗
            </button>
          )}
        </li>
      ))}
    </ol>
  );
}
