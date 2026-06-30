import { test } from "node:test";
import assert from "node:assert/strict";
import {
  deDash, wDecode, stripSectionEmoji, isoWeek, fmtUsdCompact,
  parseTokenPcts, parseMovers, parseStoryLine, marketTone, snapshotCells,
} from "../lib/weekly.js";

// ── deDash (em-dash ban; en dash preserved) ─────────────────────────────────
test("deDash replaces spaced em dash with a comma", () => {
  assert.equal(deDash("bought at market — no discount"), "bought at market, no discount");
});

test("deDash leaves en dashes (date ranges) untouched", () => {
  assert.equal(deDash("Jun 22–28"), "Jun 22–28");
});

test("deDash tidies doubled punctuation it creates", () => {
  assert.equal(deDash("rate cut — ; next week"), "rate cut; next week");
});

// ── wDecode ─────────────────────────────────────────────────────────────────
test("wDecode decodes entities", () => {
  assert.equal(wDecode("Tom &amp; Jerry &lt;b&gt;"), "Tom & Jerry <b>");
});

// ── stripSectionEmoji ───────────────────────────────────────────────────────
test("stripSectionEmoji removes a leading section glyph", () => {
  assert.equal(stripSectionEmoji("📊 Market Rotation"), "Market Rotation");
});

// ── isoWeek ─────────────────────────────────────────────────────────────────
test("isoWeek returns year + week for a Monday", () => {
  const w = isoWeek("2026-06-22");
  assert.equal(w.year, 2026);
  assert.ok(Number.isInteger(w.week) && w.week >= 1 && w.week <= 53);
});

test("isoWeek is stable across the same Mon–Sun week", () => {
  assert.equal(isoWeek("2026-06-22").week, isoWeek("2026-06-28").week);
});

test("isoWeek null input", () => {
  assert.deepEqual(isoWeek(null), { year: null, week: null });
});

// ── fmtUsdCompact ───────────────────────────────────────────────────────────
test("fmtUsdCompact scales and renders whole-dollar prices", () => {
  assert.equal(fmtUsdCompact(2.5e12), "$2.50T");
  assert.equal(fmtUsdCompact(3.7e9), "$3.7B");
  assert.equal(fmtUsdCompact(150e6), "$150M");
  assert.equal(fmtUsdCompact(60000), "$60,000");
});

test("fmtUsdCompact null for non-finite", () => {
  assert.equal(fmtUsdCompact(null), null);
  assert.equal(fmtUsdCompact(Infinity), null);
});

// ── parseTokenPcts ──────────────────────────────────────────────────────────
test("parseTokenPcts extracts ticker + signed 7d %", () => {
  assert.deepEqual(parseTokenPcts("BTC +5.2% ETH -3.1%"), [
    { sym: "BTC", pct: 5.2 }, { sym: "ETH", pct: -3.1 },
  ]);
});

test("parseTokenPcts handles underscore/digit tickers", () => {
  assert.deepEqual(parseTokenPcts("FIGR_HELOC +12%"), [{ sym: "FIGR_HELOC", pct: 12 }]);
});

// ── parseMovers ─────────────────────────────────────────────────────────────
test("parseMovers buckets by label and sorts by magnitude", () => {
  const out = parseMovers([
    "<b>Gainers:</b> ETH +3% BTC +5%",
    "<b>Losers:</b> DOGE -8% SOL -2%",
  ]);
  assert.deepEqual(out.gainers, [{ sym: "BTC", pct: 5 }, { sym: "ETH", pct: 3 }]);
  assert.deepEqual(out.losers, [{ sym: "DOGE", pct: -8 }, { sym: "SOL", pct: -2 }]);
});

test("parseMovers falls back to sign when no label", () => {
  const out = parseMovers(["AAA +4% BBB -1%"]);
  assert.equal(out.gainers[0].sym, "AAA");
  assert.equal(out.losers[0].sym, "BBB");
});

// ── parseStoryLine ──────────────────────────────────────────────────────────
test("parseStoryLine splits title and href, dropping the anchor", () => {
  const { title, href } = parseStoryLine('• Coinbase acquires Deribit <a href="https://t.co/x">↗</a>');
  assert.equal(title, "Coinbase acquires Deribit");
  assert.equal(href, "https://t.co/x");
});

// ── marketTone ──────────────────────────────────────────────────────────────
test("marketTone reads rotation label + risk regime from BTC 7d", () => {
  assert.deepEqual(marketTone("BTC", [], { btc_7d_pct: 5 }), { label: "BTC-Led", regime: "Risk-on" });
  assert.deepEqual(marketTone("ETH", [], { btc_7d_pct: -5 }), { label: "ETH-Led", regime: "Risk-off" });
  assert.deepEqual(marketTone("MIXED", [], { btc_7d_pct: 0 }), { label: "Mixed", regime: "Neutral" });
});

// ── snapshotCells (structured column path) ──────────────────────────────────
test("snapshotCells builds ordered cells from the structured snapshot", () => {
  const cells = snapshotCells({
    snapshot: {
      btc_price: 60000, btc_7d_pct: 3, eth_price: 3000, eth_7d_pct: -1,
      eth_btc_ratio: 0.05, btc_dominance: 52.1,
      total_market_cap_usd: 2.5e12, market_cap_change_24h_pct: 1.2,
    },
    marketLines: [],
    defiTvl: { tvl_now: 9.9e10, pct: 0.5 },
  });
  const byKey = Object.fromEntries(cells.map(c => [c.key, c]));
  assert.equal(byKey.btc.value, "$60,000");
  assert.equal(byKey.eth.value, "$3,000");
  assert.equal(byKey.dom.value, "52.1%");
  assert.equal(byKey.mcap.value, "$2.50T");
  assert.equal(byKey.tvl.value, "$99.0B");
});

test("snapshotCells empty when nothing is available", () => {
  assert.deepEqual(snapshotCells({ snapshot: {}, marketLines: [], defiTvl: null }), []);
});
