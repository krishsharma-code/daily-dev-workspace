import { test } from "node:test";
import assert from "node:assert/strict";

import {
  fuzzyMatch,
  fuzzyScore,
  search,
  highlightSegments,
  WEIGHTS,
} from "./fuzzy.js";

test("matches an in-order subsequence and reports indices", () => {
  const m = fuzzyMatch("fb", "Foo Bar");
  assert.ok(m, "expected a match");
  assert.deepEqual(m.indices, [0, 4]);
});

test("returns null when the query is not a subsequence", () => {
  assert.equal(fuzzyMatch("xyz", "Foo Bar"), null);
  // right characters, wrong order
  assert.equal(fuzzyMatch("bf", "Foo Bar"), null);
});

test("query longer than the text can never match", () => {
  assert.equal(fuzzyMatch("abcd", "abc"), null);
});

test("empty query matches everything with a neutral score", () => {
  const m = fuzzyMatch("", "anything");
  assert.deepEqual(m, { score: 0, indices: [] });
});

test("matching is case-insensitive by default", () => {
  assert.ok(fuzzyMatch("foo", "FOOBAR"));
  assert.ok(fuzzyMatch("FOO", "foobar"));
});

test("caseSensitive option is respected", () => {
  assert.ok(fuzzyMatch("Foo", "FooBar", { caseSensitive: true }));
  assert.equal(fuzzyMatch("foo", "FooBar", { caseSensitive: true }), null);
});

test("boundary matches outrank mid-word matches", () => {
  const boundary = fuzzyScore("fb", "Foo Bar");
  const midword = fuzzyScore("fb", "foobar");
  assert.ok(boundary > midword, `${boundary} should beat ${midword}`);
});

test("consecutive matches outrank scattered ones", () => {
  const consecutive = fuzzyScore("bar", "bar of soap");
  const scattered = fuzzyScore("bar", "b1a2r");
  assert.ok(consecutive > scattered, `${consecutive} should beat ${scattered}`);
});

test("a whole-word prefix is a strong match", () => {
  const prefix = fuzzyScore("react", "react");
  const embedded = fuzzyScore("react", "pre-react-hooks");
  assert.ok(prefix > embedded);
});

test("camelCase transitions earn a bonus", () => {
  const camel = fuzzyScore("fb", "fooBar");
  const plain = fuzzyScore("fb", "foobar");
  assert.ok(camel > plain);
});

test("fuzzyScore returns -Infinity when there is no match", () => {
  assert.equal(fuzzyScore("zzz", "abc"), -Infinity);
});

test("non-string arguments are rejected", () => {
  assert.throws(() => fuzzyMatch(5, "abc"), TypeError);
  assert.throws(() => fuzzyMatch("a", null), TypeError);
});

test("search ranks best matches first and preserves original order on ties", () => {
  const items = ["foobar", "Foo Bar", "fizz buzz", "qux"];
  const results = search(items, "fb");
  const texts = results.map((r) => r.item);
  assert.ok(texts.includes("Foo Bar"));
  assert.ok(texts.includes("foobar"));
  assert.ok(!texts.includes("qux"), "non-matches must be dropped");
  // "Foo Bar" (boundary match) should come before "foobar" (mid-word match)
  assert.ok(texts.indexOf("Foo Bar") < texts.indexOf("foobar"));
});

test("search can read text from object keys", () => {
  const people = [
    { name: "Ada Lovelace", city: "London" },
    { name: "Alan Turing", city: "Manchester" },
  ];
  const byName = search(people, "ada", { keys: ["name"] });
  assert.equal(byName.length, 1);
  assert.equal(byName[0].item.name, "Ada Lovelace");
  assert.equal(byName[0].key, "name");

  const byCity = search(people, "man", { keys: ["city"] });
  assert.equal(byCity.length, 1);
  assert.equal(byCity[0].item.city, "Manchester");
});

test("search honours the limit option", () => {
  const items = ["alpha", "alpaca", "alps", "alphabet"];
  const results = search(items, "alp", { limit: 2 });
  assert.equal(results.length, 2);
});

test("search honours the threshold option", () => {
  const items = ["Foo Bar", "foobar"];
  const strict = search(items, "fb", { threshold: fuzzyScore("fb", "Foo Bar") });
  assert.equal(strict.length, 1);
  assert.equal(strict[0].item, "Foo Bar");
});

test("search validates its input", () => {
  assert.throws(() => search("not an array", "x"), TypeError);
});

test("empty query returns every item", () => {
  const items = ["a", "b", "c"];
  assert.equal(search(items, "").length, 3);
});

test("custom weights change the ranking", () => {
  // Penalise boundary matches so the mid-word match wins instead.
  const weights = { boundary: 0, firstChar: 0, consecutive: 0, camel: 0 };
  const results = search(["Foo Bar", "foobar"], "fb", { weights });
  assert.equal(results[0].item, "foobar");
});

test("highlightSegments splits around matched indices", () => {
  const m = fuzzyMatch("fb", "Foo Bar");
  const segments = highlightSegments("Foo Bar", m.indices);
  const rebuilt = segments.map((s) => s.text).join("");
  assert.equal(rebuilt, "Foo Bar");
  assert.deepEqual(
    segments.map((s) => [s.text, s.match]),
    [["F", true], ["oo ", false], ["B", true], ["ar", false]],
  );
});

test("highlightSegments handles no matches and empty text", () => {
  assert.deepEqual(highlightSegments("abc", []), [{ text: "abc", match: false }]);
  assert.deepEqual(highlightSegments("", []), []);
});

test("WEIGHTS are exported and shape the score", () => {
  assert.equal(typeof WEIGHTS.consecutive, "number");
  const base = fuzzyScore("a", "a");
  const boosted = fuzzyScore("a", "a", { weights: { ...WEIGHTS, base: WEIGHTS.base + 10 } });
  assert.ok(boosted > base);
});
