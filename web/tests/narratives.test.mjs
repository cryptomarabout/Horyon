import { test } from "node:test";
import assert from "node:assert/strict";
import {
  trajectoryMeta, momentumMultiple, convictionTier, coverageWindow,
  evidenceMix, evidenceMixLabel, deriveSector, scoreTier, timeAgo,
} from "../lib/narratives.js";

// ── trajectoryMeta ──────────────────────────────────────────────────────────
test("trajectoryMeta maps state to institutional label", () => {
  assert.equal(trajectoryMeta("heating").label, "Accelerating");
  assert.equal(trajectoryMeta("cooling").label, "Moderating");
  assert.equal(trajectoryMeta("unknown").label, "Established");   // steady fallback
});

// ── momentumMultiple ────────────────────────────────────────────────────────
test("momentumMultiple formats with adaptive precision", () => {
  assert.equal(momentumMultiple(1.23), "1.2×");
  assert.equal(momentumMultiple(12), "12×");           // ≥10 → no decimals
});

test("momentumMultiple null for non-finite", () => {
  assert.equal(momentumMultiple(null), null);
  assert.equal(momentumMultiple(Infinity), null);
});

// ── convictionTier ──────────────────────────────────────────────────────────
test("convictionTier ladders on count AND span", () => {
  assert.equal(convictionTier({ signalCount: 12, spanDays: 14 }).label, "High");
  assert.equal(convictionTier({ signalCount: 6, spanDays: 8 }).label, "Moderate");
  assert.equal(convictionTier({ signalCount: 2, spanDays: 3 }).label, "Emerging");
  assert.equal(convictionTier().label, "Emerging");    // defaults
});

test("convictionTier needs both thresholds (count alone is not enough)", () => {
  assert.equal(convictionTier({ signalCount: 20, spanDays: 2 }).label, "Emerging");
});

// ── coverageWindow ──────────────────────────────────────────────────────────
test("coverageWindow computes span/labels deterministically", () => {
  const w = coverageWindow("2026-06-01", "2026-06-15T00:00:00");
  assert.equal(w.spanDays, 14);
  assert.equal(w.weeks, 2);
  assert.equal(w.spanLabel, "2 wks");
  assert.equal(w.sinceLabel, "Since Jun 1");
  assert.equal(w.rangeLabel, "Jun 1 – Jun 15");
});

test("coverageWindow null without firstSeen", () => {
  assert.equal(coverageWindow(null, "2026-06-15T00:00:00"), null);
});

// ── evidenceMix ─────────────────────────────────────────────────────────────
test("evidenceMix counts by signal type", () => {
  const sig = [{ signal_type: "news" }, { signal_type: "news" }, { signal_type: "podcast" }];
  assert.deepEqual(evidenceMix(sig), { news: 2, podcast: 1 });
  assert.equal(evidenceMixLabel(sig), "2 news · 1 podcast");
});

test("evidenceMix empty", () => {
  assert.deepEqual(evidenceMix([]), {});
});

// ── deriveSector (fallback classifier, mirrors python _sector) ──────────────
test("deriveSector picks the keyword-scored sector", () => {
  assert.equal(deriveSector("Stablecoin depeg risk", "", []), "Stablecoins");
  assert.equal(deriveSector("Uniswap DEX flows", "", []), "DEX & Trading");
});

test("deriveSector defaults to DeFi with no signal", () => {
  assert.equal(deriveSector("Generic headline", "", []), "DeFi");
});

// ── scoreTier ───────────────────────────────────────────────────────────────
test("scoreTier buckets", () => {
  assert.equal(scoreTier(90), "hi");
  assert.equal(scoreTier(60), "mid");
  assert.equal(scoreTier(30), "lo");
  assert.equal(scoreTier(5), "min");
});

// ── timeAgo ─────────────────────────────────────────────────────────────────
test("timeAgo handles now and null", () => {
  assert.equal(timeAgo(null), null);
  assert.equal(timeAgo(new Date().toISOString().replace("Z", "")), "now");
});
