// Shared output-sanitisation helpers for the public API routes.
// Feed content + links come from untrusted RSS sources and are rendered client-side via
// dangerouslySetInnerHTML, so these must never let raw markup or a non-http scheme through.
// Kept in one module (and unit-tested in web/tests/sanitize.test.mjs) so the XSS guard has
// a single definition.

export const escapeHtml = (s) =>
  String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// Allowlist http(s) only and escape quotes/angle brackets so a link can't break out of the
// href attribute or smuggle a javascript:/data: scheme. Returns null to drop the link.
export const safeHref = (url) => {
  const s = String(url ?? "").trim();
  if (!/^https?:\/\//i.test(s)) return null;
  return s
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
};
