/* Horyon service worker (T15) — installability + offline fallback ONLY.
 *
 * Deliberately conservative to respect two hard constraints:
 *  1. Zero-egress + freshness: /api/* is NEVER touched by the SW (search, audio, OG,
 *     suggest must always hit the network — they're precomputed but must stay live).
 *  2. Nonce'd CSP: app HTML carries a per-request script nonce (middleware.js). Caching
 *     and replaying app HTML would serve a stale nonce, so we NEVER cache navigations.
 *     The only offline fallback is the static, script-free /offline.html.
 *
 * Strategy:
 *  - install: precache the offline page + icons; activate immediately.
 *  - /api/*  : bypass entirely (default browser fetch).
 *  - navigations (HTML): network-only, fall back to /offline.html when offline.
 *  - /_next/static/* + same-origin static assets: cache-first (content-hashed → immutable).
 *  - everything else: straight to network.
 */
const CACHE = "horyon-shell-v1";
const PRECACHE = ["/offline.html", "/icon-192.png", "/icon-512.png", "/favicon.ico"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isStaticAsset(url) {
  return (
    url.pathname.startsWith("/_next/static/") ||
    /\.(?:css|js|woff2?|png|jpg|jpeg|svg|ico|webp)$/.test(url.pathname)
  );
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Only handle our own origin; never intercept /api/* (freshness + zero-egress).
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  // Navigations: network-only, static offline page as the only fallback (never cache HTML —
  // the nonce'd CSP makes a replayed app page unsafe).
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline.html")));
    return;
  }

  // Hashed static assets: cache-first, populate lazily.
  if (isStaticAsset(url)) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(CACHE).then((c) => c.put(request, copy));
            }
            return res;
          })
      )
    );
  }
});
