import { test } from "node:test";
import assert from "node:assert/strict";
import { updateSlotLabel, parseDigest } from "../lib/digest.js";

// ── updateSlotLabel (mirrors app/main._intraday_slot_label) ──────────────────
test("updateSlotLabel maps UTC hour to slot name", () => {
  assert.equal(updateSlotLabel("2026-07-21T13:00:00"), "Midday update");
  assert.equal(updateSlotLabel("2026-07-21T15:59:00"), "Midday update");
  assert.equal(updateSlotLabel("2026-07-21T19:00:00"), "Evening update");
  assert.equal(updateSlotLabel("2026-07-21T22:00:00"), "Evening update");
});

test("updateSlotLabel falls back outside the named windows", () => {
  assert.equal(updateSlotLabel("2026-07-21T07:00:00"), "Intraday update");
  assert.equal(updateSlotLabel(""), "Intraday update");
  assert.equal(updateSlotLabel(null), "Intraday update");
});

// An intraday update's stored content is the same Telegram-HTML bullet block as the
// morning digest, so the existing parseDigest handles it (the timeline relies on this).
test("parseDigest parses an intraday update body", () => {
  const body =
    "• <b>Aave Exploit</b> — Funds drained. <a href=\"https://news.example.com/x\">🔗</a>\n" +
    "• <b>Uniswap V4</b> — Now live on Base. <a href=\"https://real.example.com/a\">🔗</a>";
  const { bullets } = parseDigest(body);
  assert.equal(bullets.length, 2);
  assert.equal(bullets[0].title, "Aave Exploit");
  assert.equal(bullets[1].link, "https://real.example.com/a");
});
