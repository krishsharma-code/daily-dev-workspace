import { test } from "node:test";
import assert from "node:assert/strict";

import {
  compareNatural,
  compareValues,
  createCollator,
  createComparator,
  sortBy,
} from "./sort-kit.js";

const people = [
  { name: "Asha", role: "Engineer", salary: 90000, joined: "2021-03-04" },
  { name: "Bilal", role: "Designer", salary: 72000, joined: "2019-07-19" },
  { name: "Chetan", role: "Engineer", salary: 120000, joined: "2022-01-10" },
  { name: "Divya", role: "Manager", salary: null, joined: "2018-11-02" },
  { name: "Esha", role: "Engineer", salary: 90000, joined: "2021-03-04" },
];

// ---------------------------------------------------------------------------
// compareValues
// ---------------------------------------------------------------------------

test("compareValues orders numbers and strings by their natural type", () => {
  assert.ok(compareValues(2, 10) < 0);
  assert.equal(compareValues(7, 7), 0);
  assert.ok(compareValues("apple", "banana") < 0);
  assert.ok(compareValues("banana", "apple") > 0);
});

test("compareValues puts missing values last by default and first on request", () => {
  assert.ok(compareValues(null, "a") > 0);
  assert.ok(compareValues(undefined, 1) > 0);
  assert.ok(compareValues(NaN, 1) > 0, "NaN cannot be ordered, so it counts as missing");
  assert.equal(compareValues(null, undefined), 0);
  assert.ok(compareValues(null, "a", { nulls: "first" }) < 0);
});

test("compareValues ranks booleans, numbers, dates, strings and objects consistently", () => {
  const ordered = [
    false,
    true,
    3,
    new Date("2020-01-01"),
    "zebra",
    { note: "object" },
  ];
  for (let i = 0; i < ordered.length; i += 1) {
    for (let j = i + 1; j < ordered.length; j += 1) {
      assert.ok(
        compareValues(ordered[i], ordered[j]) < 0,
        `${String(ordered[i])} should rank before ${String(ordered[j])}`,
      );
      assert.ok(compareValues(ordered[j], ordered[i]) > 0);
    }
  }
});

test("compareValues orders dates by time, not by string form", () => {
  assert.ok(compareValues(new Date("2021-02-03"), new Date("2021-10-01")) < 0);
  assert.equal(compareValues(new Date(500), new Date(500)), 0);
});

test("compareValues treats two invalid dates as equal instead of returning NaN", () => {
  assert.equal(compareValues(new Date("nope"), new Date("also-nope")), 0);
});

test("compareValues is case-sensitive by default and can ignore case", () => {
  assert.notEqual(compareValues("a", "A"), 0);
  assert.equal(compareValues("a", "A", { sensitivity: "base" }), 0);
});

test("compareValues rejects an unknown nulls placement", () => {
  assert.throws(() => compareValues(1, 2, { nulls: "middle" }), RangeError);
});

// ---------------------------------------------------------------------------
// compareNatural
// ---------------------------------------------------------------------------

test("compareNatural compares digit runs by value", () => {
  assert.ok(compareNatural("item9", "item10") < 0);
  assert.ok(compareNatural("report10.txt", "report9.txt") > 0);
  assert.ok(compareNatural("v1.9.2", "v1.10.0") < 0);
  assert.equal(compareNatural("item7", "item7"), 0);
});

test("compareNatural prefers the shorter spelling when only padding differs", () => {
  assert.ok(compareNatural("7", "007") < 0);
  assert.ok(compareNatural("007", "7") > 0);
  assert.equal(compareNatural("007", "007"), 0);
});

test("compareNatural stringifies non-strings so mixed input still orders", () => {
  assert.ok(compareNatural(2, 10) < 0);
  assert.ok(compareNatural(10, "9") > 0);
});

test("compareNatural places missing values last unless told otherwise", () => {
  assert.ok(compareNatural(null, "a") > 0);
  assert.ok(compareNatural(null, "a", { nulls: "first" }) < 0);
  assert.equal(compareNatural(undefined, null), 0);
});

// ---------------------------------------------------------------------------
// createCollator
// ---------------------------------------------------------------------------

test("createCollator reuses collators for the same configuration", () => {
  assert.equal(createCollator({ locale: "en-IN" }), createCollator({ locale: "en-IN" }));
  assert.notEqual(
    createCollator({ locale: "en-IN" }),
    createCollator({ locale: "en-IN", numeric: true }),
  );
});

test("createCollator reports an unusable locale instead of failing silently", () => {
  assert.throws(() => createCollator({ locale: "not a tag!" }), RangeError);
});

// ---------------------------------------------------------------------------
// createComparator
// ---------------------------------------------------------------------------

test("createComparator sorts by a single property path", () => {
  const compare = createComparator("name");
  const sorted = people.slice().sort(compare);
  assert.deepEqual(
    sorted.map((p) => p.name),
    ["Asha", "Bilal", "Chetan", "Divya", "Esha"],
  );
});

test("createComparator follows dotted paths and missing branches", () => {
  const rows = [
    { user: { name: "Ravi" } },
    { user: { name: "Anil" } },
    { user: null },
  ];
  const sorted = sortBy(rows, "user.name");
  assert.deepEqual(sorted.map((row) => row.user?.name ?? null), ["Anil", "Ravi", null]);
});

test("createComparator applies a per-key direction", () => {
  const sorted = sortBy(people, [
    { key: "role" },
    { key: "salary", direction: "desc" },
  ]);
  assert.deepEqual(
    sorted.map((p) => `${p.role}:${p.name}`),
    [
      "Designer:Bilal",
      "Engineer:Chetan",
      "Engineer:Asha",
      "Engineer:Esha",
      "Manager:Divya",
    ],
  );
});

test("createComparator keeps blanks in place when a key is reversed", () => {
  const ascending = sortBy(people, "salary");
  const descending = sortBy(people, { key: "salary", direction: "desc" });

  assert.deepEqual(ascending.map((p) => p.salary), [72000, 90000, 90000, 120000, null]);
  assert.deepEqual(descending.map((p) => p.salary), [120000, 90000, 90000, 72000, null]);
});

test("createComparator can place blanks first, in both directions", () => {
  const ascending = sortBy(people, { key: "salary", nulls: "first" });
  const descending = sortBy(people, { key: "salary", nulls: "first", direction: "desc" });
  assert.equal(ascending[0].name, "Divya");
  assert.equal(descending[0].name, "Divya");
});

test("createComparator accepts accessor functions and mixes them with paths", () => {
  const sorted = sortBy(people, [
    { by: (p) => p.name.length },
    "name",
  ]);
  assert.deepEqual(
    sorted.map((p) => p.name),
    ["Asha", "Esha", "Bilal", "Divya", "Chetan"],
  );
});

test("createComparator coerces values for a typed number key", () => {
  const rows = [
    { id: "row", amount: "1,200".replace(",", "") },
    { id: "row", amount: "300" },
    { id: "row", amount: "" },
  ];
  const sorted = sortBy(rows, { key: "amount", type: "number" });
  assert.deepEqual(sorted.map((row) => row.amount), ["300", "1200", ""]);
});

test("createComparator treats unparsable numbers as missing", () => {
  const rows = [{ v: "ten" }, { v: "2" }, { v: "1" }];
  const sorted = sortBy(rows, { key: "v", type: "number" });
  assert.deepEqual(sorted.map((row) => row.v), ["1", "2", "ten"]);
});

test("createComparator supports natural ordering per key", () => {
  const files = [{ name: "file10.md" }, { name: "file9.md" }, { name: "file1.md" }];
  assert.deepEqual(
    sortBy(files, { key: "name", type: "natural" }).map((f) => f.name),
    ["file1.md", "file9.md", "file10.md"],
  );
  assert.deepEqual(
    sortBy(files, { key: "name" }).map((f) => f.name),
    ["file1.md", "file10.md", "file9.md"],
  );
});

test("createComparator returns 0 only when every key ties", () => {
  const compare = createComparator(["role", "salary"]);
  assert.equal(compare(people[0], people[4]), 0);
  assert.notEqual(compare(people[0], people[1]), 0);
});

test("createComparator rejects malformed keys", () => {
  assert.throws(() => createComparator(), TypeError);
  assert.throws(() => createComparator([]), RangeError);
  assert.throws(() => createComparator(""), TypeError);
  assert.throws(() => createComparator(42), TypeError);
  assert.throws(() => createComparator({ key: "a", by: () => 1 }), TypeError);
  assert.throws(() => createComparator({ direction: "desc" }), TypeError);
  assert.throws(() => createComparator({ key: "a", by: "nope" }), TypeError);
  assert.throws(() => createComparator({ key: "a", direction: "up" }), RangeError);
  assert.throws(() => createComparator({ key: "a", nulls: "middle" }), RangeError);
  assert.throws(() => createComparator({ key: "a", type: "date" }), RangeError);
});

test("createComparator rejects malformed options", () => {
  assert.throws(() => createComparator("a", "desc"), TypeError);
  assert.throws(() => createComparator("a", { direction: "up" }), RangeError);
  assert.throws(() => createComparator("a", { numeric: "yes" }), TypeError);
  assert.equal(typeof createComparator("a", null), "function");
});

// ---------------------------------------------------------------------------
// sortBy
// ---------------------------------------------------------------------------

test("sortBy returns a new array and leaves the input untouched", () => {
  const input = people.slice();
  const output = sortBy(input, "name");
  assert.notEqual(output, input);
  assert.deepEqual(input.map((p) => p.name), ["Asha", "Bilal", "Chetan", "Divya", "Esha"]);
  assert.equal(output[0], input[0], "items are returned by reference, not cloned");
});

test("sortBy is stable across equal keys", () => {
  const engineers = sortBy(people, "role").filter((p) => p.role === "Engineer");
  assert.deepEqual(engineers.map((p) => p.name), ["Asha", "Chetan", "Esha"]);

  const bySalary = sortBy(people, "salary");
  const tied = bySalary.filter((p) => p.salary === 90000);
  assert.deepEqual(tied.map((p) => p.name), ["Asha", "Esha"]);
});

test("sortBy handles empty and single-item arrays", () => {
  assert.deepEqual(sortBy([], "name"), []);
  const one = [{ name: "only" }];
  const result = sortBy(one, "name");
  assert.deepEqual(result, one);
  assert.notEqual(result, one);
});

test("sortBy rejects a non-array input", () => {
  assert.throws(() => sortBy("nope", "name"), TypeError);
  assert.throws(() => sortBy(null, "name"), TypeError);
});

test("sortBy can order primitives directly with an identity accessor", () => {
  assert.deepEqual(sortBy([3, 1, 2], (value) => value), [1, 2, 3]);
  assert.deepEqual(
    sortBy(["item10", "item9"], (value) => value, { type: "natural" }),
    ["item9", "item10"],
  );
});

test("sortBy composes with a plain object key and a global direction", () => {
  const sorted = sortBy(people, "joined", { direction: "desc" });
  assert.deepEqual(
    sorted.map((p) => p.joined),
    ["2022-01-10", "2021-03-04", "2021-03-04", "2019-07-19", "2018-11-02"],
  );
});

test("sortBy keeps a total order when a column holds mixed types", () => {
  const rows = [{ v: "beta" }, { v: 10 }, { v: null }, { v: 2 }, { v: true }];
  const sorted = sortBy(rows, "v");
  assert.deepEqual(sorted.map((row) => row.v), [true, 2, 10, "beta", null]);
});

test("sortBy is repeatable: the same input always yields the same order", () => {
  const rows = [
    { a: 1, b: "x" },
    { a: 1, b: "y" },
    { a: 0, b: "z" },
    { a: 1, b: "x" },
  ];
  const first = sortBy(rows, ["a", "b"]);
  const second = sortBy(rows.slice().reverse(), ["a", "b"]);
  assert.deepEqual(first.map((r) => `${r.a}${r.b}`), ["0z", "1x", "1x", "1y"]);
  assert.deepEqual(second.map((r) => `${r.a}${r.b}`), ["0z", "1x", "1x", "1y"]);
});

test("sortBy leaves a comparator usable inside Array.prototype.sort", () => {
  const compare = createComparator("salary");
  const sorted = [...people].sort(compare);
  assert.equal(sorted.at(-1).name, "Divya", "the blank salary still sorts last");
});
