// Shared inline SVG icons — one definition per glyph so every surface renders
// the same mark. Pure presentational components (no hooks/browser APIs), safe to
// import from server or client components. Size-specific call sites pass `size`.

// X / Twitter logo.
export function XIcon({ size = 10 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"
      aria-hidden="true" style={{ flexShrink: 0, display: "block" }}>
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.742l7.737-8.835L1.254 2.25H8.08l4.259 5.626L18.243 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

// "Open in new tab" external-link arrow.
export function ExtIcon({ size = 8 }) {
  return (
    <svg
      width={size} height={size}
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ flexShrink: 0, display: "block" }}
    >
      <path d="M5 2H2a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V7" />
      <path d="M8 1h3v3" />
      <line x1="11" y1="1" x2="5.5" y2="6.5" />
    </svg>
  );
}

// Right-pointing chevron — used in list items to indicate clickability.
export function ChevronIcon({ size = 9 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="4,2 8,6 4,10" />
    </svg>
  );
}

// Microphone — used to mark podcast feed items.
export function MicIcon({ size = 11 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="5.5" y="1.5" width="5" height="8" rx="2.5" />
      <path d="M3.5 7.5a4.5 4.5 0 0 0 9 0" />
      <line x1="8" y1="12" x2="8" y2="14.5" />
    </svg>
  );
}

// Magnifier — used in panel search headers, toolbars, and the nav search bar.
export function SearchIcon({ size = 11 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0 }}>
      <circle cx="6.5" cy="6.5" r="4.5" />
      <line x1="10.5" y1="10.5" x2="14" y2="14" />
    </svg>
  );
}

// Downward caret — used in dropdown triggers (mobile page select, etc.).
export function CaretIcon({ size = 10 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}

// Funnel — used to mark the source filter trigger.
export function FilterIcon({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2 3.5h12L9.5 8.5V13l-3 1.5V8.5L2 3.5z" />
    </svg>
  );
}

// ── Audio player icons ────────────────────────────────────────────────────────

// Filled play triangle — audio play button.
export function PlayIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 18" fill="currentColor"
      aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
      <path d="M2 1.5L14 9L2 16.5Z" />
    </svg>
  );
}

// Filled pause bars — audio pause button.
export function PauseIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 14 18" fill="currentColor"
      aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
      <rect x="0.5" y="0.5" width="4.5" height="17" rx="1.5" />
      <rect x="9" y="0.5" width="4.5" height="17" rx="1.5" />
    </svg>
  );
}

// Counterclockwise circular arrow — skip back in audio.
export function SkipBackIcon({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 .49-3.51" />
    </svg>
  );
}

// Clockwise circular arrow — skip forward in audio.
export function SkipFwdIcon({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-.49-3.51" />
    </svg>
  );
}

// Up-pointing chevron — collapse the expanded audio player.
export function ChevronUpIcon({ size = 11 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
      <path d="M4 10l4-4 4 4" />
    </svg>
  );
}

// ⓘ info — methodology / help affordance.
export function InfoIcon({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
      <circle cx="8" cy="8" r="6.5" />
      <line x1="8" y1="7.2" x2="8" y2="11.5" />
      <circle cx="8" cy="4.8" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

// × dismiss — close/stop the persistent audio bar.
export function DismissIcon({ size = 11 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round"
      aria-hidden="true" style={{ display: "block", flexShrink: 0 }}>
      <line x1="3" y1="3" x2="13" y2="13" />
      <line x1="13" y1="3" x2="3" y2="13" />
    </svg>
  );
}
