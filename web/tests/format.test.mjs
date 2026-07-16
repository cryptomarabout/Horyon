import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fmtTvl, fmtPrice, fmtAgo, fmtShortDate, fmtDayAgo, fmtWeekRange, getDomain,
} from "../lib/format.js";

// ── fmtTvl ──────────────────────────────────────────────────────────────────
test("fmtTvl scales T/B/M and plain dollars", () => {
  assert.equal(fmtTvl(2.5e12), "$2.50T");
  assert.equal(fmtTvl(3.7e9), "$3.7B");
  assert.equal(fmtTvl(150e6), "$150M");
  assert.equal(fmtTvl(50_000), "$50,000");
});

test("fmtTvl returns null for nullish or non-positive", () => {
  assert.equal(fmtTvl(null), null);
  assert.equal(fmtTvl(undefined), null);
  assert.equal(fmtTvl(0), null);
  assert.equal(fmtTvl(-5), null);
});

// ── fmtPrice ────────────────────────────────────────────────────────────────
test("fmtPrice uses 2 decimals at or above a dollar", () => {
  assert.equal(fmtPrice(150.5), "$150.50");
  assert.equal(fmtPrice(1), "$1.00");
});

test("fmtPrice uses significant figures below a dollar", () => {
  // A sub-cent altcoin price rounded to 2dp would print as $0.00 — useless.
  assert.equal(fmtPrice(0.0001234), "$0.0001234");
});

test("fmtPrice returns null for nullish input", () => {
  assert.equal(fmtPrice(null), null);
  assert.equal(fmtPrice(undefined), null);
});

// ── fmtAgo ──────────────────────────────────────────────────────────────────
test("fmtAgo formats relative buckets", () => {
  assert.equal(fmtAgo(new Date().toISOString()), "now");
  assert.equal(fmtAgo(new Date(Date.now() - 30 * 60_000).toISOString()), "30m ago");
  assert.equal(fmtAgo(new Date(Date.now() - 2 * 3_600_000).toISOString()), "2h ago");
  assert.equal(fmtAgo(new Date(Date.now() - 3 * 86_400_000).toISOString()), "3d ago");
});

test("fmtAgo returns null for missing/invalid", () => {
  assert.equal(fmtAgo(null), null);
  assert.equal(fmtAgo("not-a-date"), null);
});

// ── fmtShortDate ────────────────────────────────────────────────────────────
test("fmtShortDate renders 'Mon D'", () => {
  assert.equal(fmtShortDate("2026-06-21"), "Jun 21");
  assert.equal(fmtShortDate(""), "");
});

// ── fmtDayAgo ───────────────────────────────────────────────────────────────
test("fmtDayAgo today / 1d / Nd", () => {
  assert.equal(fmtDayAgo(new Date().toISOString()), "today");
  assert.equal(fmtDayAgo(new Date(Date.now() - 86_400_000).toISOString()), "1d ago");
  assert.equal(fmtDayAgo(new Date(Date.now() - 5 * 86_400_000).toISOString()), "5d ago");
});

test("fmtDayAgo falls back to a date past 30 days", () => {
  const out = fmtDayAgo(new Date(Date.now() - 40 * 86_400_000).toISOString());
  assert.match(out, /^[A-Z][a-z]{2} \d+$/);   // e.g. "May 19"
});

test("fmtDayAgo null for missing", () => {
  assert.equal(fmtDayAgo(null), null);
});

// ── fmtWeekRange ────────────────────────────────────────────────────────────
test("fmtWeekRange same month collapses", () => {
  assert.equal(fmtWeekRange("2026-06-05", "2026-06-11"), "Jun 5–11, 2026");
});

test("fmtWeekRange cross month spells both", () => {
  assert.equal(fmtWeekRange("2026-05-28", "2026-06-03"), "May 28 – Jun 3, 2026");
});

test("fmtWeekRange empty for bad input", () => {
  assert.equal(fmtWeekRange("", ""), "");
});

// ── getDomain ───────────────────────────────────────────────────────────────
test("getDomain strips www", () => {
  assert.equal(getDomain("https://www.theblock.co/post/1"), "theblock.co");
  assert.equal(getDomain("https://coindesk.com/markets"), "coindesk.com");
});

test("getDomain null for bad input", () => {
  assert.equal(getDomain("not a url"), null);
  assert.equal(getDomain(null), null);
});
