import { test } from "node:test";
import assert from "node:assert/strict";

import {
  CsvParseError,
  detectDelimiter,
  fromObjects,
  parseCsv,
  parseRows,
  stringifyCsv,
  toObjects,
} from "./csv-table.js";

// ---------------------------------------------------------------------------
// Parsing basics
// ---------------------------------------------------------------------------

test("parses a plain comma-separated document", () => {
  const { delimiter, rows } = parseRows("a,b,c\n1,2,3\n");
  assert.equal(delimiter, ",");
  assert.deepEqual(rows, [
    ["a", "b", "c"],
    ["1", "2", "3"],
  ]);
});

test("ignores a trailing newline instead of emitting an empty record", () => {
  assert.deepEqual(parseRows("a,b\n").rows, [["a", "b"]]);
  assert.deepEqual(parseRows("a,b").rows, [["a", "b"]]);
});

test("keeps a trailing empty field", () => {
  assert.deepEqual(parseRows("a,\n").rows, [["a", ""]]);
  assert.deepEqual(parseRows("a,,").rows, [["a", "", ""]]);
});

test("handles CRLF and lone CR line endings", () => {
  assert.deepEqual(parseRows("a,b\r\nc,d\r\n").rows, [
    ["a", "b"],
    ["c", "d"],
  ]);
  assert.deepEqual(parseRows("a,b\rc,d\r").rows, [
    ["a", "b"],
    ["c", "d"],
  ]);
});

test("strips a leading byte-order mark", () => {
  assert.deepEqual(parseRows("\uFEFFname,age\nAsha,31\n").rows, [
    ["name", "age"],
    ["Asha", "31"],
  ]);
});

test("keeps delimiters and newlines inside quoted fields", () => {
  const csv = 'name,note\nAsha,"likes commas, semicolons; and\ntabs"\n';
  assert.deepEqual(parseRows(csv).rows, [
    ["name", "note"],
    ["Asha", "likes commas, semicolons; and\ntabs"],
  ]);
});

test("unescapes doubled quotes", () => {
  assert.deepEqual(parseRows('a,"say ""hi"""\n').rows, [["a", 'say "hi"']]);
  assert.deepEqual(parseRows('"""quoted"""').rows, [['"quoted"']]);
});

test("parses an empty quoted field as an empty string", () => {
  assert.deepEqual(parseRows('a,"",b').rows, [["a", "", "b"]]);
});

test("recovers from a stray quote inside an unquoted field by default", () => {
  assert.deepEqual(parseRows('5" screen,ok').rows, [['5" screen', "ok"]]);
});

test("drops blank records but keeps single-field ones", () => {
  assert.deepEqual(parseRows("a,b\n\n\nc,d\n").rows, [
    ["a", "b"],
    ["c", "d"],
  ]);
  assert.deepEqual(parseRows("a\n\nb\n", { skipEmptyLines: false }).rows, [["a"], [""], ["b"]]);
});

test("trims unquoted fields only when asked", () => {
  assert.deepEqual(parseRows(" a , b ").rows, [[" a ", " b "]]);
  assert.deepEqual(parseRows(" a , b ", { trim: true }).rows, [["a", "b"]]);
  assert.deepEqual(parseRows('" a ", b', { trim: true }).rows, [[" a ", "b"]]);
});

// ---------------------------------------------------------------------------
// Malformed input
// ---------------------------------------------------------------------------

test("throws a positioned error for an unterminated quoted field", () => {
  assert.throws(
    () => parseRows('a,"b\nc'),
    (error) => {
      assert.ok(error instanceof CsvParseError);
      assert.equal(error.line, 2);
      assert.match(error.message, /Unterminated quoted field/);
      return true;
    },
  );
});

test("throws on a stray quote when strict", () => {
  assert.throws(() => parseRows('a,b"c', { strict: true }), CsvParseError);
});

test("throws on a row wider than the header when strict", () => {
  assert.throws(
    () => parseCsv("a,b\n1,2,3\n", { strict: true }),
    (error) => {
      assert.ok(error instanceof CsvParseError);
      assert.equal(error.line, 2);
      assert.match(error.message, /3 fields but the header has 2/);
      return true;
    },
  );
});

test("rejects invalid options", () => {
  assert.throws(() => parseRows("a,b", { delimiter: "" }), TypeError);
  assert.throws(() => parseRows("a,b", { delimiter: ",," }), RangeError);
  assert.throws(() => parseRows("a,b", { quote: "''" }), TypeError);
  assert.throws(() => parseRows("a,b", { trim: "yes" }), TypeError);
  assert.throws(() => parseRows("a,b", null), TypeError);
  assert.throws(() => parseRows("a,b", { delimiter: '"' }), RangeError);
});

// ---------------------------------------------------------------------------
// Delimiter detection
// ---------------------------------------------------------------------------

test("detects the usual delimiters", () => {
  assert.equal(detectDelimiter("a,b\n1,2"), ",");
  assert.equal(detectDelimiter("a;b\n1;2"), ";");
  assert.equal(detectDelimiter("a\tb\n1\t2"), "\t");
  assert.equal(detectDelimiter("a|b\n1|2"), "|");
});

test("a delimiter inside quoted prose does not win", () => {
  const tsv = 'name\tnote\nAsha\t"likes commas, really"\nRavi\t"also, yes"\n';
  assert.equal(detectDelimiter(tsv), "\t");
});

test("falls back to a comma for single-column text", () => {
  assert.equal(detectDelimiter("only\none\ncolumn"), ",");
  assert.equal(detectDelimiter(""), ",");
});

test("prefers the comma when candidates score the same", () => {
  assert.equal(detectDelimiter("a,b;c\nd,e;f"), ",");
});

test("parseCsv reports the delimiter it settled on", () => {
  const parsed = parseCsv("city;state\nPune;Maharashtra\n");
  assert.equal(parsed.delimiter, ";");
  assert.deepEqual(parsed.headers, ["city", "state"]);
});

// ---------------------------------------------------------------------------
// Headers
// ---------------------------------------------------------------------------

test("splits the header row from the data", () => {
  const parsed = parseCsv("name,age\nAsha,31\nRavi,29\n");
  assert.deepEqual(parsed.headers, ["name", "age"]);
  assert.deepEqual(parsed.rows, [
    ["Asha", "31"],
    ["Ravi", "29"],
  ]);
});

test("names empty headers and de-duplicates repeats", () => {
  const parsed = parseCsv("name,,name\nAsha,x,Asha\n");
  assert.deepEqual(parsed.headers, ["name", "column2", "name_2"]);
});

test("pads short rows and keeps long ones intact", () => {
  const parsed = parseCsv("a,b,c\n1,2\n4,5,6,7\n");
  assert.deepEqual(parsed.rows, [
    ["1", "2", ""],
    ["4", "5", "6", "7"],
  ]);
});

test("returns every record as data when header is false", () => {
  const parsed = parseCsv("1,2\n3,4\n", { header: false });
  assert.equal(parsed.headers, null);
  assert.deepEqual(parsed.rows, [
    ["1", "2"],
    ["3", "4"],
  ]);
});

test("parses an empty document into nothing", () => {
  const parsed = parseCsv("");
  assert.deepEqual(parsed.headers, []);
  assert.deepEqual(parsed.rows, []);
});

// ---------------------------------------------------------------------------
// Objects
// ---------------------------------------------------------------------------

test("maps rows onto objects keyed by header", () => {
  const { headers, rows } = parseCsv("city,state\nPune,Maharashtra\n");
  assert.deepEqual(toObjects(headers, rows), [{ city: "Pune", state: "Maharashtra" }]);
});

test("toObjects fills missing trailing values with empty strings", () => {
  assert.deepEqual(toObjects(["a", "b"], [["1"]]), [{ a: "1", b: "" }]);
});

test("fromObjects infers headers in first-seen key order", () => {
  const result = fromObjects([{ city: "Pune", state: "MH" }, { state: "KA", city: "Bengaluru" }]);
  assert.deepEqual(result.headers, ["city", "state"]);
  assert.deepEqual(result.rows, [
    ["Pune", "MH"],
    ["Bengaluru", "KA"],
  ]);
});

test("fromObjects honours an explicit header list and blanks missing keys", () => {
  const result = fromObjects([{ city: "Pune" }], { headers: ["city", "state"] });
  assert.deepEqual(result.rows, [["Pune", ""]]);
});

test("fromObjects rejects non-object entries", () => {
  assert.throws(() => fromObjects([1]), TypeError);
});

// ---------------------------------------------------------------------------
// Serialising
// ---------------------------------------------------------------------------

test("writes plain fields without quotes and ends with one newline", () => {
  assert.equal(stringifyCsv([["a", "b"], ["1", "2"]]), "a,b\n1,2\n");
});

test("quotes only the fields that need it", () => {
  const text = stringifyCsv([["plain", "has, comma", 'has "quote"', "has\nnewline", " padded "]]);
  assert.equal(text, 'plain,"has, comma","has ""quote""","has\nnewline"," padded "\n');
});

test("quoteAll quotes every field, including empty ones", () => {
  assert.equal(stringifyCsv([["a", ""]], { quoteAll: true }), '"a",""\n');
});

test("writes a header line for object rows", () => {
  const text = stringifyCsv([{ city: "Pune", state: "MH" }], { headers: ["city", "state"] });
  assert.equal(text, "city,state\nPune,MH\n");
});

test("respects a custom delimiter and line ending", () => {
  const text = stringifyCsv([["a", "b"]], { delimiter: ";", newline: "\r\n" });
  assert.equal(text, "a;b\r\n");
});

test("renders null and undefined as empty fields", () => {
  assert.equal(stringifyCsv([[null, undefined, 0, false]]), ",,0,false\n");
});

test("serialising no rows produces an empty string", () => {
  assert.equal(stringifyCsv([]), "");
  assert.equal(stringifyCsv([], { headers: ["a"] }), "a\n");
});

test("rejects invalid serialising options", () => {
  assert.throws(() => stringifyCsv("nope"), TypeError);
  assert.throws(() => stringifyCsv([], { newline: "\n\n" }), TypeError);
  assert.throws(() => stringifyCsv([], { quoteAll: 1 }), TypeError);
  assert.throws(() => stringifyCsv([], { headers: "city" }), TypeError);
});

// ---------------------------------------------------------------------------
// Round trip
// ---------------------------------------------------------------------------

test("parse then stringify reproduces the document", () => {
  const source = 'name,note,count\nAsha,"likes commas, semicolons; and\ntabs",3\nRavi,plain,0\n';
  const parsed = parseCsv(source);
  const rebuilt = stringifyCsv(parsed.rows, { headers: parsed.headers });
  assert.equal(rebuilt, source);
});

test("an explicitly quoted empty field is written back bare", () => {
  // `""` and an empty field parse to the same value, so the writer picks the
  // shorter of the two forms; the data is unchanged.
  const parsed = parseCsv('name,note\nRavi,""\n');
  assert.deepEqual(parsed.rows, [["Ravi", ""]]);
  assert.equal(stringifyCsv(parsed.rows, { headers: parsed.headers }), "name,note\nRavi,\n");
});

test("objects survive a full round trip", () => {
  const people = [
    { name: 'Asha "A" Sharma', city: "Pune", note: "line one\nline two" },
    { name: "Ravi", city: "Bengaluru", note: "" },
  ];
  const { headers, rows } = fromObjects(people);
  const parsed = parseCsv(stringifyCsv(rows, { headers }));
  assert.deepEqual(toObjects(parsed.headers, parsed.rows), people);
});

test("a semicolon document round trips with its own delimiter", () => {
  const source = "city;state\nPune;MH\n";
  const parsed = parseCsv(source);
  assert.equal(stringifyCsv(parsed.rows, { headers: parsed.headers, delimiter: parsed.delimiter }), source);
});
