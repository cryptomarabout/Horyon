import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fmt, fmtLen, chapterAt, chapterDeepLink, parseAudioDeepLink,
} from "../lib/audio.js";

// ── fmt / fmtLen (existing helpers, quick pins) ──────────────────────────────
test("fmt renders m:ss and guards NaN", () => {
  assert.equal(fmt(0), "0:00");
  assert.equal(fmt(75), "1:15");
  assert.equal(fmt(NaN), "0:00");
});

test("fmtLen rounds to minutes", () => {
  assert.equal(fmtLen(90), "~2 min");
  assert.equal(fmtLen(0), "");
});

// ── chapterAt ────────────────────────────────────────────────────────────────
test("chapterAt finds the containing chapter", () => {
  const chaps = [{ start: 0 }, { start: 60 }, { start: 120 }];
  assert.equal(chapterAt(chaps, 0), 0);
  assert.equal(chapterAt(chaps, 59), 0);
  assert.equal(chapterAt(chaps, 61), 1);
  assert.equal(chapterAt(chaps, 999), 2);
});

// ── chapterDeepLink (T15) ────────────────────────────────────────────────────
test("chapterDeepLink builds a stable per-day permalink", () => {
  assert.equal(
    chapterDeepLink("2026-07-15", "explainer", 312.7),
    "/d/2026-07-15?variant=explainer&t=312"
  );
});

test("chapterDeepLink floors negative/garbage time to 0 and defaults bad variant", () => {
  assert.equal(chapterDeepLink("2026-07-15", "bogus", -5),
    "/d/2026-07-15?variant=standard&t=0");
  assert.equal(chapterDeepLink("2026-07-15", "standard", "x"),
    "/d/2026-07-15?variant=standard&t=0");
});

// ── parseAudioDeepLink (T15) ─────────────────────────────────────────────────
const params = (obj) => ({ get: (k) => (k in obj ? obj[k] : null) });

test("parseAudioDeepLink reads a valid variant + time", () => {
  assert.deepEqual(
    parseAudioDeepLink(params({ variant: "explainer", t: "312" })),
    { variant: "explainer", t: 312 }
  );
});

test("parseAudioDeepLink rejects unknown variant and bad time", () => {
  assert.deepEqual(parseAudioDeepLink(params({ variant: "hack", t: "abc" })),
    { variant: null, t: null });
  assert.deepEqual(parseAudioDeepLink(params({ t: "-3" })),
    { variant: null, t: null });
});

test("parseAudioDeepLink floors a fractional time", () => {
  assert.deepEqual(parseAudioDeepLink(params({ t: "45.9" })), { variant: null, t: 45 });
});

test("parseAudioDeepLink tolerates a missing params object", () => {
  assert.deepEqual(parseAudioDeepLink(null), { variant: null, t: null });
  assert.deepEqual(parseAudioDeepLink({}), { variant: null, t: null });
});

// round-trip: a link built by chapterDeepLink parses back to the same values
test("chapterDeepLink → parseAudioDeepLink round-trips", () => {
  const href = chapterDeepLink("2026-07-15", "standard", 200);
  const qs = new URLSearchParams(href.split("?")[1]);
  assert.deepEqual(parseAudioDeepLink(qs), { variant: "standard", t: 200 });
});
