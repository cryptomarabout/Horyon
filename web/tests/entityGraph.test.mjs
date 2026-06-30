import { test } from "node:test";
import assert from "node:assert/strict";
import {
  radiusFor, monogram, edgeKey, safeId, arcPath, avatarCandidates,
  R_MIN, R_MAX,
} from "../lib/entityGraph.js";

// ── radiusFor ───────────────────────────────────────────────────────────────
test("radiusFor midpoint when range is degenerate", () => {
  assert.equal(radiusFor(5, 10, 10), (R_MIN + R_MAX) / 2);
});

test("radiusFor at the floor and ceiling of the mention range", () => {
  assert.equal(radiusFor(1, 1, 100), R_MIN);       // mc == minMc → rMin
  assert.equal(radiusFor(100, 1, 100), R_MAX);     // mc == maxMc → rMax
});

test("radiusFor respects the board cap", () => {
  assert.equal(radiusFor(100, 1, 100, 12), 12);    // min(26, cap)
});

// ── monogram ────────────────────────────────────────────────────────────────
test("monogram uses two-word initials else first two chars", () => {
  assert.equal(monogram("Uniswap Labs"), "UL");
  assert.equal(monogram("Aave"), "AA");
  assert.equal(monogram(""), "?");
});

// ── edgeKey (order-independent) ─────────────────────────────────────────────
test("edgeKey is canonical regardless of arg order", () => {
  assert.equal(edgeKey("aave", "base"), "aave|base");
  assert.equal(edgeKey("base", "aave"), "aave|base");
});

// ── safeId ──────────────────────────────────────────────────────────────────
test("safeId sanitises non-id chars", () => {
  assert.equal(safeId("aave.fi"), "egclip-aave_fi");
  assert.equal(safeId("a/b c"), "egclip-a_b_c");
});

// ── arcPath ─────────────────────────────────────────────────────────────────
test("arcPath emits a quadratic bezier through a bowed control point", () => {
  assert.equal(arcPath(0, 0, 10, 0), "M0,0Q5,1 10,0");
});

// ── avatarCandidates (ordered fallback chain) ───────────────────────────────
test("avatarCandidates prefers mirrored avatar, then logo, then unavatar", () => {
  const out = avatarCandidates({
    avatarCached: true, slug: "aave", logoUrl: "https://logo/aave.png", twitterHandle: "@aave",
  });
  assert.deepEqual(out, [
    "/api/avatar/aave",
    "https://logo/aave.png",
    "https://unavatar.io/twitter/aave?fallback=false",
  ]);
});

test("avatarCandidates skips mirrored entry when not cached", () => {
  const out = avatarCandidates({ logoUrl: "https://logo/x.png" });
  assert.deepEqual(out, ["https://logo/x.png"]);
});

test("avatarCandidates empty for a bare node", () => {
  assert.deepEqual(avatarCandidates({}), []);
});
