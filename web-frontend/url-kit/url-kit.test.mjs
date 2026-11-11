import test from "node:test";
import assert from "node:assert/strict";

import {
  encodeQueryComponent,
  decodeQueryComponent,
  parseQuery,
  stringifyQuery,
  getQuery,
  setQuery,
  removeQuery,
  mergeQuery,
  parseUrl,
  resolveUrl,
  isAbsoluteUrl,
  isSameOrigin,
  normalizeUrl,
} from "./url-kit.js";

test("encodeQueryComponent keeps the unreserved set", () => {
  assert.equal(encodeQueryComponent("AZaz09-._~"), "AZaz09-._~");
});

test("encodeQueryComponent encodes RFC 3986 sub-delimiters that encodeURIComponent leaves raw", () => {
  assert.equal(encodeURIComponent("!'()*"), "!'()*");
  assert.equal(encodeQueryComponent("!'()*"), "%21%27%28%29%2A");
  assert.equal(encodeQueryComponent("it's (a) test!"), "it%27s%20%28a%29%20test%21");
});

test("encodeQueryComponent encodes a space as %20 unless asked for +", () => {
  assert.equal(encodeQueryComponent("a b"), "a%20b");
  assert.equal(encodeQueryComponent("a b", { spaceAsPlus: true }), "a+b");
});

test("encodeQueryComponent handles non-ASCII and astral characters", () => {
  assert.equal(encodeQueryComponent("₹"), "%E2%82%B9");
  assert.equal(encodeQueryComponent("नमस्ते"), "%E0%A4%A8%E0%A4%AE%E0%A4%B8%E0%A5%8D%E0%A4%A4%E0%A5%87");
  assert.equal(encodeQueryComponent("😀"), "%F0%9F%98%80");
});

test("decodeQueryComponent round-trips what the encoder produced", () => {
  for (const value of ["plain", "a b", "it's (a) test!", "₹ 1,234", "😀 नमस्ते", "a+b"]) {
    assert.equal(decodeQueryComponent(encodeQueryComponent(value)), value);
  }
});

test("decodeQueryComponent treats + as space and %2B as a literal plus", () => {
  assert.equal(decodeQueryComponent("a+b"), "a b");
  assert.equal(decodeQueryComponent("a%2Bb"), "a+b");
  assert.equal(decodeQueryComponent("a+b", { plusAsSpace: false }), "a+b");
});

test("decodeQueryComponent never throws on malformed input", () => {
  assert.equal(decodeQueryComponent("%"), "%");
  assert.equal(decodeQueryComponent("%zz"), "%zz");
  assert.equal(decodeQueryComponent("%E0%A4"), "%E0%A4");
  assert.equal(decodeQueryComponent("100%"), "100%");
  assert.equal(decodeQueryComponent("%FF%FE"), "%FF%FE");
});

test("decodeQueryComponent still decodes the valid parts around a broken sequence", () => {
  assert.equal(decodeQueryComponent("%41%E0%A4%42"), "A%E0%A4B");
  assert.equal(decodeQueryComponent("50%25+off"), "50% off");
});

test("parseQuery reads repeated keys into arrays", () => {
  assert.deepEqual(parseQuery("a=1&a=2&b=3"), { a: ["1", "2"], b: "3" });
  assert.deepEqual(parseQuery("?a=1"), { a: "1" });
});

test("parseQuery honours the duplicates strategy", () => {
  assert.deepEqual(parseQuery("a=1&a=2", { duplicates: "first" }), { a: "1" });
  assert.deepEqual(parseQuery("a=1&a=2", { duplicates: "last" }), { a: "2" });
});

test("parseQuery builds nested objects and arrays from bracket notation", () => {
  assert.deepEqual(parseQuery("tag[]=a&tag[]=b&filter[status]=open"), {
    tag: ["a", "b"],
    filter: { status: "open" },
  });
  assert.deepEqual(parseQuery("f[a][b]=1&f[a][c]=2"), { f: { a: { b: "1", c: "2" } } });
  assert.deepEqual(parseQuery("list[0]=x&list[1]=y"), { list: ["x", "y"] });
  assert.deepEqual(parseQuery("rows[][id]=1&rows[][id]=2"), { rows: [{ id: "1" }, { id: "2" }] });
});

test("parseQuery can be told to ignore brackets", () => {
  assert.deepEqual(parseQuery("a[b]=1", { brackets: false }), { "a[b]": "1" });
});

test("parseQuery keeps valueless keys and empty values distinct from absence", () => {
  assert.deepEqual(parseQuery("a&b="), { a: "", b: "" });
  assert.equal("c" in parseQuery("a&b="), false);
});

test("parseQuery accepts a full URL, a leading ? and a hash", () => {
  const expected = { page: "2" };
  assert.deepEqual(parseQuery("https://example.com/x?page=2#top"), expected);
  assert.deepEqual(parseQuery("?page=2"), expected);
  assert.deepEqual(parseQuery("page=2#top"), expected);
  assert.deepEqual(parseQuery(""), {});
  assert.deepEqual(parseQuery("?"), {});
});

test("parseQuery decodes keys and values, and skips empty pairs", () => {
  assert.deepEqual(parseQuery("a%20b=c%20d&&e=1"), { "a b": "c d", e: "1" });
  assert.deepEqual(parseQuery("q=it%27s+fine"), { q: "it's fine" });
});

test("parseQuery refuses prototype pollution", () => {
  const polluted = parseQuery("__proto__[polluted]=yes&a=1");
  assert.deepEqual(polluted, { a: "1" });
  assert.equal({}.polluted, undefined);

  const flat = parseQuery("__proto__=x&constructor=y&prototype[z]=1&ok=1");
  assert.deepEqual(flat, { ok: "1" });
  assert.equal({}.polluted, undefined);
});

test("parseQuery caps nesting depth and keeps a too-deep key literal", () => {
  const key = "a" + "[b]".repeat(8);
  assert.deepEqual(parseQuery(`${key}=1`), { [key]: "1" });
  assert.deepEqual(parseQuery("a[b][c][d]=1"), { a: { b: { c: { d: "1" } } } });
});

test("parseQuery keeps a malformed bracket key whole", () => {
  assert.deepEqual(parseQuery("a[b][=1"), { "a[b][": "1" });
});

test("stringifyQuery encodes strictly by default", () => {
  assert.equal(stringifyQuery({ q: "a b", s: "it's" }), "q=a%20b&s=it%27s");
  assert.equal(stringifyQuery({ q: "a b" }, { spaceAsPlus: true }), "q=a+b");
});

test("stringifyQuery drops null and undefined, formats scalars", () => {
  assert.equal(stringifyQuery({ a: null, b: undefined, c: false, d: 0, e: "x" }), "c=false&d=0&e=x");
  assert.equal(stringifyQuery({ a: null }, { skipNull: false }), "a=");
  assert.equal(
    stringifyQuery({ at: new Date("2024-01-31T00:00:00Z") }),
    "at=2024-01-31T00%3A00%3A00.000Z",
  );
});

test("stringifyQuery supports the four array formats", () => {
  const params = { tag: ["a", "b"] };
  assert.equal(stringifyQuery(params), "tag%5B%5D=a&tag%5B%5D=b");
  assert.equal(stringifyQuery(params, { arrayFormat: "repeat" }), "tag=a&tag=b");
  assert.equal(stringifyQuery(params, { arrayFormat: "indices" }), "tag%5B0%5D=a&tag%5B1%5D=b");
  assert.equal(stringifyQuery(params, { arrayFormat: "comma" }), "tag=a%2Cb");
});

test("stringifyQuery builds nested params", () => {
  assert.equal(
    stringifyQuery({ filter: { status: "open", owner: "me" }, page: 2 }),
    "filter%5Bstatus%5D=open&filter%5Bowner%5D=me&page=2",
  );
  assert.equal(stringifyQuery({ empty: [], gone: {} }), "");
});

test("stringifyQuery sorts keys when asked", () => {
  assert.equal(stringifyQuery({ b: 2, a: 1 }, { sort: true }), "a=1&b=2");
  assert.equal(stringifyQuery({ b: 2, a: 1 }), "b=2&a=1");
});

test("parseQuery and stringifyQuery round-trip nested string params", () => {
  const params = {
    tag: ["a", "b"],
    filter: { status: "open", q: "it's fine" },
    page: "2",
    empty: "",
  };
  const query = stringifyQuery(params);
  assert.deepEqual(parseQuery(query), {
    tag: ["a", "b"],
    filter: { status: "open", q: "it's fine" },
    page: "2",
    empty: "",
  });
});

test("parseQuery and stringifyQuery round-trip indexed arrays", () => {
  const query = stringifyQuery({ list: ["x", "y"] }, { arrayFormat: "indices" });
  assert.deepEqual(parseQuery(query), { list: ["x", "y"] });
});

test("getQuery returns the value, an array, or undefined", () => {
  assert.equal(getQuery("https://e.com/?a=1&b=2", "a"), "1");
  assert.deepEqual(getQuery("/x?a=1&a=2", "a"), ["1", "2"]);
  assert.equal(getQuery("/x?a=1", "z"), undefined);
  assert.equal(getQuery("/x?a%5B%5D=1", "a[]"), "1");
  assert.equal(getQuery("/x?a[b]=1", "a"), undefined);
  assert.equal(getQuery("/x?q=it%27s+fine", "q"), "it's fine");
});

test("setQuery replaces only its own pairs and keeps the rest byte-for-byte", () => {
  const url = "https://e.com/p?keep=%2Fraw%20value&a=1&b=2#top";
  assert.equal(
    setQuery(url, "a", "9"),
    "https://e.com/p?keep=%2Fraw%20value&b=2&a=9#top",
  );
});

test("setQuery removes bracket children of the key it replaces", () => {
  const url = "/x?f[a]=1&f[b]=2&keep=1";
  assert.equal(setQuery(url, "f", { c: "3" }), "/x?keep=1&f%5Bc%5D=3");
});

test("setQuery appends new params and handles arrays", () => {
  assert.equal(setQuery("/x", "q", "a b"), "/x?q=a%20b");
  assert.equal(setQuery("/x", "tag", ["a", "b"]), "/x?tag%5B%5D=a&tag%5B%5D=b");
  assert.equal(setQuery("/x", "tag", ["a", "b"], { arrayFormat: "repeat" }), "/x?tag=a&tag=b");
});

test("setQuery with null removes the param", () => {
  assert.equal(setQuery("/x?a=1&b=2", "a", null), "/x?b=2");
  assert.equal(setQuery("/x?a=1", "a", null), "/x");
});

test("setQuery leaves the fragment and path alone", () => {
  assert.equal(setQuery("https://e.com/a/b#sec?x=1", "y", "2"), "https://e.com/a/b?y=2#sec?x=1");
});

test("removeQuery drops every pair for the key", () => {
  assert.equal(removeQuery("/x?a=1&a=2&b=3", "a"), "/x?b=3");
  assert.equal(removeQuery("/x?b=3", "a"), "/x?b=3");
  assert.equal(removeQuery("/x?a=1#h", "a"), "/x#h");
});

test("mergeQuery applies each key in order", () => {
  assert.equal(
    mergeQuery("/search?q=old&page=1", { q: "new", tag: ["a"], page: null }),
    "/search?q=new&tag%5B%5D=a",
  );
});

test("parseUrl exposes decoded params and URL parts", () => {
  const parsed = parseUrl("https://e.com:443/a/b?x=1&y=2#h");
  assert.equal(parsed.href, "https://e.com/a/b?x=1&y=2#h");
  assert.equal(parsed.origin, "https://e.com");
  assert.equal(parsed.pathname, "/a/b");
  assert.equal(parsed.hash, "#h");
  assert.deepEqual(parsed.params, { x: "1", y: "2" });
});

test("resolveUrl resolves relative references and returns null when invalid", () => {
  assert.equal(resolveUrl("/a/b", "https://e.com/c/d"), "https://e.com/a/b");
  assert.equal(resolveUrl("../x?y=1", "https://e.com/a/b/c"), "https://e.com/a/x?y=1");
  assert.equal(resolveUrl("https://e.com/z"), "https://e.com/z");
  assert.equal(resolveUrl("/a/b"), null);
});

test("isAbsoluteUrl recognises schemes and protocol-relative URLs", () => {
  assert.equal(isAbsoluteUrl("https://e.com"), true);
  assert.equal(isAbsoluteUrl("mailto:a@e.com"), true);
  assert.equal(isAbsoluteUrl("//cdn.e.com/x.js"), true);
  assert.equal(isAbsoluteUrl("/path"), false);
  assert.equal(isAbsoluteUrl("path"), false);
});

test("isSameOrigin compares origins, resolving against a base", () => {
  assert.equal(isSameOrigin("https://e.com/a", "https://e.com/b"), true);
  assert.equal(isSameOrigin("https://e.com", "http://e.com"), false);
  assert.equal(isSameOrigin("https://e.com", "https://cdn.e.com"), false);
  assert.equal(isSameOrigin("/a", "/b", "https://e.com"), true);
  assert.equal(isSameOrigin("not a url", "https://e.com"), false);
});

test("normalizeUrl sorts params and can strip the fragment", () => {
  assert.equal(
    normalizeUrl("https://e.com/p?b=2&a=1#top"),
    "https://e.com/p?a=1&b=2#top",
  );
  assert.equal(
    normalizeUrl("https://e.com/p?b=2&a=1#top", { stripHash: true }),
    "https://e.com/p?a=1&b=2",
  );
  assert.equal(normalizeUrl("https://e.com/p?"), "https://e.com/p");
  assert.equal(normalizeUrl("HTTPS://E.com:443/p"), "https://e.com/p");
});
