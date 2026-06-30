// Validation for the admin-gated thread composer (PATCH /api/thread). Kept in one module
// (unit-tested in web/tests/validateThread.test.mjs) so the request-shape contract the
// composer + external poster rely on has a single definition.

// Each tweet row must have the shape downstream relies on. The composer always sends `text`
// (string); `link`/`importance_score` are optional but, when present, must be the right type.
// Returns an error string, or null when the tweet is valid.
export function badTweet(t) {
  if (t === null || typeof t !== "object" || Array.isArray(t)) return "each tweet must be an object";
  if (typeof t.text !== "string") return "tweet.text must be a string";
  if (t.link != null && typeof t.link !== "string") return "tweet.link must be a string";
  if (t.importance_score != null && typeof t.importance_score !== "number") return "tweet.importance_score must be a number";
  return null;
}
