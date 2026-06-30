import { createPortal } from "react-dom";

// Portals a dropdown / bottom sheet to <body> with a click-catching backdrop.
// The masthead and feed-head surfaces carry a backdrop-filter, which would
// otherwise trap a position:fixed child inside its containing block — so every
// anchored menu must render at the body root. Pair with useAnchoredMenu.
export default function MenuPortal({
  open, onClose, backdropClass, className, style, role, ariaLabel, children,
}) {
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <>
      <div className={backdropClass} onClick={onClose} aria-hidden />
      <div className={className} style={style} role={role} aria-label={ariaLabel}>
        {children}
      </div>
    </>,
    document.body
  );
}
