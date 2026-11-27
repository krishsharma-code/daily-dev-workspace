import { test } from "node:test";
import assert from "node:assert/strict";

import {
  annotate,
  countMatches,
  escapeHtml,
  findMatches,
  highlightHtml,
  parseQuery,
  snippet,
  snippetHtml,
} from "./snippet-kit.js";

const INVOICE =
  "Customer raised a ticket about the GST invoice not matching the purchase order. " +
  "The GST invoice was regenerated on 12 November and the credit note followed a day later.";

// ---------------------------------------------------------------------------
// parseQuery
// ---------------------------------------------------------------------------

test("parseQuery splits words and keeps quoted phrases intact", () => {
  assert.deepEqual(
    parseQuery('"gst invoice" refund'),
    [
      { text: "gst invoice", phrase: true },
      { text: "refund", phrase: false },
    ],
  );
});

test("parseQuery drops duplicates case-insensitively and puts longer terms first", () => {
  const terms = parseQuery("GST gst invoice");
  assert.deepEqual(terms.map((t) => t.text), ["invoice", "GST"]);
});

test("parseQuery tolerates extra whitespace and empty queries", () => {
  assert.deepEqual(parseQuery("   "), []);
  assert.deepEqual(parseQuery("  gst   refund  ").map((t) => t.text), ["refund", "gst"]);
});

// ---------------------------------------------------------------------------
// findMatches
// ---------------------------------------------------------------------------

test("findMatches is case-insensitive by default and exact when asked", () => {
  const text = "GST invoice for the gst period";
  assert.deepEqual(findMatches(text, "gst"), [
    { start: 0, end: 3 },
    { start: 20, end: 23 },
  ]);
  assert.deepEqual(findMatches(text, "gst", { caseSensitive: true }), [{ start: 20, end: 23 }]);
});

test("findMatches keeps separate words separate but merges touching ranges", () => {
  assert.deepEqual(findMatches("gst invoice", ["gst", "invoice"]), [
    { start: 0, end: 3 },
    { start: 4, end: 11 },
  ]);
  assert.deepEqual(findMatches("gst invoice", '"gst invoice"'), [{ start: 0, end: 11 }], "a phrase is one range");
  assert.deepEqual(findMatches("abcdef", ["abc", "cde"]), [{ start: 0, end: 5 }], "overlap merges");
  assert.deepEqual(findMatches("aaaa", "aa"), [{ start: 0, end: 4 }], "overlapping occurrences merge");
});

test("findMatches honours wholeWord", () => {
  const text = "gst gstr gst";
  assert.deepEqual(findMatches(text, "gst", { wholeWord: true }), [
    { start: 0, end: 3 },
    { start: 9, end: 12 },
  ]);
});

test("findMatches returns nothing for empty text, empty query or absent terms", () => {
  assert.deepEqual(findMatches("", "gst"), []);
  assert.deepEqual(findMatches("gst", ""), []);
  assert.deepEqual(findMatches("gst", []), []);
  assert.deepEqual(findMatches("gst", "refund"), []);
});

test("findMatches keeps offsets valid on Indic text and emoji", () => {
  const text = "कर चालान 🧾 में त्रुटि है, चालान दोबारा भेजें";
  const ranges = findMatches(text, "चालान");
  assert.equal(ranges.length, 2);
  for (const range of ranges) {
    assert.equal(text.slice(range.start, range.end), "चालान");
  }
  const emoji = findMatches("payment 🧾 failed 🧾 again", "🧾");
  assert.equal(emoji.length, 2);
  assert.equal("payment 🧾 failed 🧾 again".slice(emoji[1].start, emoji[1].end), "🧾");
});

test("findMatches accepts a query string or an array of term objects", () => {
  assert.deepEqual(findMatches("gst invoice", '"gst invoice"'), [{ start: 0, end: 11 }]);
  assert.deepEqual(findMatches("gst invoice", [{ text: "invoice", phrase: false }]), [{ start: 4, end: 11 }]);
});

test("countMatches counts merged ranges", () => {
  assert.equal(countMatches(INVOICE, "invoice"), 2);
  assert.equal(countMatches(INVOICE, '"gst invoice"'), 2, "the phrase occurs twice");
  assert.equal(countMatches(INVOICE, "gst invoice"), 4, "an unquoted query is one term per word");
  assert.equal(countMatches(INVOICE, "refund"), 0);
});

test("findMatches rejects non-string text", () => {
  assert.throws(() => findMatches(null, "gst"), TypeError);
});

// ---------------------------------------------------------------------------
// annotate / escapeHtml / highlightHtml
// ---------------------------------------------------------------------------

test("annotate alternates plain and matched segments that rebuild the text", () => {
  const segments = annotate("GST invoice raised", "invoice");
  assert.deepEqual(segments, [
    { text: "GST ", match: false },
    { text: "invoice", match: true },
    { text: " raised", match: false },
  ]);
  assert.equal(segments.map((s) => s.text).join(""), "GST invoice raised");
});

test("annotate of a fully matched text is a single matched segment", () => {
  assert.deepEqual(annotate("gst", "gst"), [{ text: "gst", match: true }]);
  assert.deepEqual(annotate("", "gst"), []);
});

test("escapeHtml escapes the five HTML-significant characters", () => {
  assert.equal(escapeHtml(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
});

test("highlightHtml wraps matches and escapes surrounding markup", () => {
  const html = highlightHtml("<script>alert('gst')</script>", "gst");
  assert.equal(html, "&lt;script&gt;alert(&#39;<mark>gst</mark>&#39;)&lt;/script&gt;");
  assert.ok(!html.includes("<script"), "user content is never emitted as markup");
});

test("highlightHtml honours tag and className, escaping the class name", () => {
  assert.equal(
    highlightHtml("gst", "gst", { tag: "em", className: 'hit"x' }),
    '<em class="hit&quot;x">gst</em>',
  );
});

test("highlightHtml with no match returns escaped text only", () => {
  assert.equal(highlightHtml("a & b", "zzz"), "a &amp; b");
});

// ---------------------------------------------------------------------------
// snippet
// ---------------------------------------------------------------------------

test("snippet returns short text whole with no ellipsis", () => {
  const result = snippet("GST invoice raised", "invoice");
  assert.deepEqual(result, {
    text: "GST invoice raised",
    ranges: [{ start: 4, end: 11 }],
    leadingEllipsis: false,
    trailingEllipsis: false,
    matched: 1,
  });
});

test("snippet windows around the match and flags both trimmed ends", () => {
  const filler = "lorem ipsum dolor sit amet ".repeat(20);
  const text = `${filler}the GST invoice is attached ${filler}`;
  const result = snippet(text, "invoice", { length: 80 });

  assert.ok(result.text.length <= 80 + 24, "window stays near the budget");
  assert.ok(result.text.includes("invoice"), "the match survives the window");
  assert.ok(result.leadingEllipsis, "left side was trimmed");
  assert.ok(result.trailingEllipsis, "right side was trimmed");
  assert.ok(!result.text.startsWith(" "), "the window does not open on a space");
  assert.ok(result.ranges.length >= 1);
  for (const range of result.ranges) {
    assert.equal(result.text.slice(range.start, range.end), "invoice");
  }
});

test("snippet does not trim the side that already starts the text", () => {
  const result = snippet(`${"GST invoice details ".repeat(10)}tail`, "GST", { length: 60 });
  assert.equal(result.leadingEllipsis, false);
  assert.ok(result.trailingEllipsis);
});

test("snippet with no match returns the head of the text", () => {
  const text = "word ".repeat(60);
  const result = snippet(text, "absent", { length: 40 });
  assert.equal(result.matched, 0);
  assert.deepEqual(result.ranges, []);
  assert.equal(result.leadingEllipsis, false);
  assert.ok(result.trailingEllipsis);
  assert.ok(result.text.length <= 40);
  assert.ok(result.text.endsWith("word"), "cut lands on a word boundary");
});

test("snippet never splits a surrogate pair", () => {
  const text = "🧾".repeat(40) + " gst " + "🧾".repeat(40);
  const result = snippet(text, "gst", { length: 9 });
  assert.ok(!/^[\uDC00-\uDFFF]/.test(result.text), "window does not start on a low surrogate");
  assert.ok(!/[\uD800-\uDBFF]$/.test(result.text), "window does not end on a high surrogate");
  assert.ok(result.text.includes("gst"));
});

test("snippet collapses runs of whitespace before cutting", () => {
  const result = snippet(`GST\n\n   invoice   raised`, "invoice");
  assert.equal(result.text, "GST invoice raised");
});

test("snippet can keep the original whitespace", () => {
  const result = snippet("GST\n\ninvoice", "invoice", { normalizeWhitespace: false });
  assert.equal(result.text, "GST\n\ninvoice");
});

test("snippet re-bases ranges onto the window it returns", () => {
  const text = `${"filler ".repeat(40)}GST invoice${" filler".repeat(40)}`;
  const result = snippet(text, "invoice", { length: 60 });
  const [range] = result.ranges;
  assert.equal(result.text.slice(range.start, range.end), "invoice");
  assert.ok(range.start > 0 && range.end < result.text.length);
});

test("snippet keeps a match longer than the budget whole", () => {
  const phrase = "a very long quoted search phrase that exceeds the window budget";
  const result = snippet(`prefix ${phrase} suffix`, `"${phrase}"`, { length: 10 });
  assert.ok(result.text.includes(phrase));
});

test("snippet handles a query with no terms", () => {
  const result = snippet("GST invoice", "");
  assert.deepEqual(result.ranges, []);
  assert.equal(result.text, "GST invoice");
});

test("snippetHtml escapes content and marks matches with ellipses", () => {
  const filler = "lorem ipsum dolor sit amet ".repeat(12);
  const html = snippetHtml(`${filler}<b>GST invoice</b>${filler}`, "invoice", { length: 60 });
  assert.ok(html.includes("<mark>invoice</mark>"));
  assert.ok(html.includes("&lt;b&gt;"), "markup in the source text is escaped");
  assert.ok(!html.includes("<b>"));
  assert.ok(html.startsWith("…"), "leading ellipsis is rendered");
  assert.ok(html.endsWith("…"), "trailing ellipsis is rendered");
});

test("snippetHtml accepts a custom tag and ellipsis", () => {
  const html = snippetHtml(`${"pad ".repeat(30)}gst${" pad".repeat(30)}`, "gst", {
    length: 20,
    tag: "strong",
    className: "hit",
    ellipsis: "...",
  });
  assert.ok(html.includes('<strong class="hit">gst</strong>'));
  assert.ok(html.startsWith("..."));
});

test("snippetHtml of a short string is just highlighted text", () => {
  assert.equal(snippetHtml("GST invoice", "invoice"), "GST <mark>invoice</mark>");
});
