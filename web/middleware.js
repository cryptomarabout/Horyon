import { NextResponse } from "next/server";
import { isValidDigestDate } from "./lib/digest";

// ── Rate limiting (public, unauthenticated API) ──────────────────────────────
// The read site is public, so /api/* is reachable by anyone. The routes are already
// DoS-resistant by design (parameterized SQL, bounded input, no per-request external
// calls), but a request-volume flood could still pile load on Postgres. A small in-memory
// fixed-window limiter per IP caps that. State lives in this module: the site runs as a
// single Next process behind Caddy, so one counter map is authoritative (it resets on
// restart — acceptable for abuse mitigation, not billing).
const WINDOW_MS = 60_000;
const LIMITS = [
  // Most expensive route (runs a Postgres FTS): tightest cap.
  { test: (p) => p === "/api/search", max: 30 },
  // Entity-map avatars: one cheap single-row bytea read by PK, and the map legitimately
  // fires up to a few hundred in the first-load burst. Browser-cached for a day after, so
  // the burst is one-off per visitor — give it plenty of headroom above the generic cap.
  { test: (p) => p.startsWith("/api/avatar/"), max: 600 },
  // Everything else under /api/* (suggest is in-memory, og/audio are cached): generous,
  // just enough to blunt a scraping flood without touching normal interactive use.
  { test: (p) => p.startsWith("/api/"), max: 120 },
];

const hits = new Map(); // key `${ip}:${max}` -> { count, resetAt }

function clientIp(request) {
  const xff = request.headers.get("x-forwarded-for");
  if (xff) return xff.split(",")[0].trim();
  return request.headers.get("x-real-ip") || "unknown";
}

// Returns null if allowed, or a 429 NextResponse if the IP is over the limit.
function rateLimit(request) {
  const path = request.nextUrl.pathname;
  const rule = LIMITS.find((r) => r.test(path));
  if (!rule) return null;

  const now = Date.now();
  const key = `${clientIp(request)}:${rule.max}`;
  let rec = hits.get(key);
  if (!rec || now >= rec.resetAt) {
    rec = { count: 0, resetAt: now + WINDOW_MS };
    hits.set(key, rec);
    // Opportunistic prune so the map can't grow unbounded under churn.
    if (hits.size > 10_000) {
      for (const [k, v] of hits) if (now >= v.resetAt) hits.delete(k);
    }
  }
  rec.count += 1;
  if (rec.count > rule.max) {
    const retryAfter = Math.max(1, Math.ceil((rec.resetAt - now) / 1000));
    return NextResponse.json(
      { error: "Too many requests" },
      { status: 429, headers: { "Retry-After": String(retryAfter) } }
    );
  }
  return null;
}

// Per-request nonce-based CSP. This replaces the static CSP that used to live in the
// Caddyfile (which required script-src 'unsafe-inline'). Next 14 propagates the nonce
// from the request's Content-Security-Policy header to its own framework scripts, and
// app/layout.js stamps the same nonce on the FOUC theme script — so no inline script
// needs 'unsafe-inline' anymore. (style-src keeps 'unsafe-inline': Next injects inline
// styles and that is not a script-execution vector.)
export function middleware(request) {
  // Cheap abuse guard first — bail before any CSP/nonce work if the IP is over its cap.
  const limited = rateLimit(request);
  if (limited) return limited;

  const nonce = btoa(crypto.randomUUID());
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https:",
    // The browser only ever calls same-origin /api/* — no cross-origin fetch/XHR/WS.
    // Locking connect-src to 'self' removes an exfiltration channel if an XSS ever slips
    // past the nonce'd script-src.
    "connect-src 'self'",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");

  // Forward the nonce + CSP on the request so Next can read it and tag its scripts.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", csp);

  // A structurally-invalid digest date (/d/garbage, /d/2026-13-99) must return a real 404,
  // not a soft-200. The /d/[date] route can't enforce this itself — its loading.js streams a
  // 200 shell before notFound() runs — so rewrite to an unmatched path here, which Next
  // renders as the styled not-found page with a 404 status. (Valid-format dates that simply
  // have no digest yet are left to the route's own notFound.)
  const dm = request.nextUrl.pathname.match(/^\/d\/([^/]+)\/?$/);
  let invalidDate = false;
  if (dm) {
    let seg = null;
    try { seg = decodeURIComponent(dm[1]); } catch { invalidDate = true; }
    if (seg !== null && !isValidDigestDate(seg)) invalidDate = true;
  }

  const response = invalidDate
    ? NextResponse.rewrite(new URL("/_digest-not-found", request.url), {
        request: { headers: requestHeaders },
      })
    : NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("content-security-policy", csp);
  return response;
}

export const config = {
  matcher: [
    // All routes except static assets. Skip prefetch requests so a prefetched RSC payload
    // (built with a different nonce) can't cause a hydration mismatch on navigation.
    {
      source:
        "/((?!_next/static|_next/image|favicon.ico|favicon-32x32.png|apple-touch-icon.png|falcon.png).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
