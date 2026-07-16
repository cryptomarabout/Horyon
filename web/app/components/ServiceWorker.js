"use client";

import { useEffect } from "react";

// Registers the PWA service worker (public/sw.js) after load. Renders nothing.
// The registration runs from a nonce'd bundle (not an inline script), so it satisfies
// the CSP without any policy change. Failures are swallowed — the SW is progressive
// enhancement (installability + offline fallback), never required for the app to work.
export default function ServiceWorker() {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    const register = () =>
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    if (document.readyState === "complete") register();
    else {
      window.addEventListener("load", register);
      return () => window.removeEventListener("load", register);
    }
  }, []);
  return null;
}
