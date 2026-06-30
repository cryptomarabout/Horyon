import { useRef, useCallback } from "react";

/**
 * Horizontal swipe → step left/right, for touch devices.
 *
 * Returns `{ onTouchStart, onTouchEnd }` to spread onto the swipeable element.
 * A swipe is only honoured when it is clearly horizontal (so it never hijacks
 * the feed's vertical scroll), long enough, and quick enough. No-op for
 * mouse/desktop (touch events simply don't fire there) and when `enabled` is
 * false (e.g. a full-screen panel is open over the feed).
 *
 * Direction follows the on-screen chevrons: swipe LEFT → `onLeft` (advance,
 * the › / newer side); swipe RIGHT → `onRight` (go back, the ‹ / older side).
 *
 * @param {() => void} onLeft   fired on a leftward swipe
 * @param {() => void} onRight  fired on a rightward swipe
 * @param {boolean}    enabled  whether swipes should be acted on (default true)
 */
export default function useSwipeNav(onLeft, onRight, enabled = true) {
  const start = useRef(null);

  const onTouchStart = useCallback((e) => {
    if (e.touches.length !== 1) { start.current = null; return; }
    const t = e.touches[0];
    start.current = { x: t.clientX, y: t.clientY, at: Date.now() };
  }, []);

  const onTouchEnd = useCallback((e) => {
    const s = start.current;
    start.current = null;
    if (!s || !enabled) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - s.x;
    const dy = t.clientY - s.y;
    if (Math.abs(dx) < 60) return;                 // too short
    if (Math.abs(dx) < Math.abs(dy) * 1.5) return; // mostly vertical → it's a scroll
    if (Date.now() - s.at > 700) return;           // too slow to be a swipe
    if (dx < 0) onLeft?.();
    else onRight?.();
  }, [onLeft, onRight, enabled]);

  return { onTouchStart, onTouchEnd };
}
