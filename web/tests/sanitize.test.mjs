import { test } from "node:test";
import assert from "node:assert/strict";
import { escapeHtml, safeHref } from "../lib/sanitize.mjs";

test("escapeHtml escapes &, <, >", () => {
  assert.equal(escapeHtml("a & b < c > d"), "a &amp; b &lt; c &gt; d");
});

test("escapeHtml coerces null/undefined to empty", () => {
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
});

test("safeHref allows http and https", () => {
  assert.equal(safeHref("https://example.com/x"), "https://example.com/x");
  assert.equal(safeHref("http://example.com"), "http://example.com");
});

test("safeHref drops javascript: and data: schemes", () => {
  assert.equal(safeHref("javascript:alert(1)"), null);
  assert.equal(safeHref("data:text/html,<script>"), null);
  assert.equal(safeHref("  vbscript:msgbox  "), null);
});

test("safeHref escapes quotes and angle brackets so it can't break out of href", () => {
  assert.equal(
    safeHref('https://x.com/"><img src=x onerror=alert(1)>'),
    "https://x.com/&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"
  );
});

test("safeHref returns null for empty/non-string", () => {
  assert.equal(safeHref(""), null);
  assert.equal(safeHref(null), null);
  assert.equal(safeHref(undefined), null);
});
