import { test } from "node:test";
import assert from "node:assert/strict";
import { badTweet } from "../lib/validateThread.mjs";

test("accepts a minimal valid tweet", () => {
  assert.equal(badTweet({ text: "hello" }), null);
});

test("accepts optional link + importance_score of correct types", () => {
  assert.equal(badTweet({ text: "x", link: "https://a.b", importance_score: 42 }), null);
  assert.equal(badTweet({ text: "x", link: null, importance_score: null }), null);
});

test("rejects non-objects", () => {
  assert.match(badTweet(null), /must be an object/);
  assert.match(badTweet("a string"), /must be an object/);
  assert.match(badTweet([1, 2]), /must be an object/);
});

test("rejects missing or non-string text", () => {
  assert.match(badTweet({}), /text must be a string/);
  assert.match(badTweet({ text: 123 }), /text must be a string/);
});

test("rejects wrong-typed link", () => {
  assert.match(badTweet({ text: "x", link: 5 }), /link must be a string/);
});

test("rejects wrong-typed importance_score", () => {
  assert.match(badTweet({ text: "x", importance_score: "high" }), /importance_score must be a number/);
});
