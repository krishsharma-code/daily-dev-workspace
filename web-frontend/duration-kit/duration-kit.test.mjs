import test from "node:test";
import assert from "node:assert/strict";

import Duration, {
  Duration as NamedDuration,
  parseDuration,
  formatDuration,
  humanizeDuration,
  toMilliseconds,
  fromMilliseconds,
  addDuration,
  compareDurations,
} from "./duration-kit.js";

const MS_SECOND = 1000;
const MS_MINUTE = 60 * MS_SECOND;
const MS_HOUR = 60 * MS_MINUTE;
const MS_DAY = 24 * MS_HOUR;

test("the default export is the Duration class", () => {
  assert.equal(Duration, NamedDuration);
  assert.equal(parseDuration("PT1S") instanceof Duration, true);
});

test("parses every component of a full duration", () => {
  const value = parseDuration("P1Y2M3D");
  assert.deepEqual(value.toObject(), {
    sign: 1,
    years: 1,
    months: 2,
    weeks: 0,
    days: 3,
    hours: 0,
    minutes: 0,
    seconds: 0,
  });
});

test("parses date and time components together", () => {
  const value = parseDuration("P1Y2M3DT4H5M6S");
  assert.equal(value.years, 1);
  assert.equal(value.months, 2);
  assert.equal(value.days, 3);
  assert.equal(value.hours, 4);
  assert.equal(value.minutes, 5);
  assert.equal(value.seconds, 6);
  assert.equal(value.sign, 1);
});

test("parses negative and explicitly positive durations", () => {
  assert.equal(parseDuration("-PT30M").sign, -1);
  assert.equal(parseDuration("+PT30M").sign, 1);
  assert.equal(parseDuration("PT30M").sign, 1);
});

test("parses decimal fractions with a point or a comma", () => {
  assert.equal(parseDuration("PT1.5H").hours, 1.5);
  assert.equal(parseDuration("PT0,25S").seconds, 0.25);
  assert.equal(parseDuration("P0.5D").days, 0.5);
});

test("trims surrounding whitespace", () => {
  assert.equal(parseDuration("  P2D\n").days, 2);
});

test("zero durations parse and normalise to a positive sign", () => {
  const zero = parseDuration("PT0S");
  assert.equal(zero.isEmpty, true);
  assert.equal(zero.sign, 1);
  assert.equal(parseDuration("-PT0S").sign, 1);
  assert.equal(formatDuration(zero), "PT0S");
});

test("rejects malformed strings", () => {
  for (const bad of [
    "",
    "P",
    "PT",
    "P1DT",
    "1Y",
    "P1S",
    "PT1D",
    "P-1D",
    "P1Y2",
    "PT1H30",
    "P1.5Y2M",
    "P1W2D",
    "PT1H1.5M30S",
    "P1Y2M3DT4H5M6S7X",
  ]) {
    assert.throws(() => parseDuration(bad), SyntaxError, `expected ${JSON.stringify(bad)} to fail`);
  }
});

test("rejects non-string input", () => {
  for (const bad of [undefined, null, 42, {}, ["PT1S"]]) {
    assert.throws(() => parseDuration(bad), TypeError);
  }
});

test("formats a full duration", () => {
  assert.equal(
    formatDuration(parseDuration("P1Y2M3DT4H5M6S")),
    "P1Y2M3DT4H5M6S",
  );
});

test("formatting drops zero components and keeps weeks as weeks", () => {
  assert.equal(formatDuration({ years: 1, seconds: 0 }), "P1Y");
  assert.equal(formatDuration({ hours: 2 }), "PT2H");
  assert.equal(formatDuration({ weeks: 3 }), "P3W");
  assert.equal(formatDuration({}), "PT0S");
  assert.equal(formatDuration({ sign: -1, days: 1 }), "-P1D");
});

test("formatting keeps fractional values clean", () => {
  assert.equal(formatDuration({ seconds: 0.1 + 0.2 }), "PT0.3S");
  assert.equal(formatDuration({ hours: 1.5 }), "PT1.5H");
});

test("parse and format round-trip", () => {
  for (const text of [
    "P1Y",
    "P1Y2M3DT4H5M6S",
    "PT0.5S",
    "-P2W",
    "P10DT12H",
    "PT90M",
    "-PT1.25H",
  ]) {
    assert.equal(formatDuration(parseDuration(text)), text);
  }
});

test("Duration.from accepts strings, objects, numbers and durations", () => {
  const fromString = Duration.from("PT1H");
  assert.equal(fromString.hours, 1);
  assert.equal(Duration.from({ minutes: 30 }).minutes, 30);
  assert.equal(Duration.from(MS_HOUR).hours, 1);
  assert.equal(Duration.from(fromString), fromString);
  assert.equal(Duration.parse("P1D").days, 1);
  assert.throws(() => Duration.from(true), TypeError);
});

test("durations are immutable", () => {
  const value = parseDuration("PT1H");
  assert.equal(Object.isFrozen(value), true);
  assert.throws(() => {
    "use strict";
    value.hours = 5;
  }, TypeError);
  assert.equal(value.hours, 1);
});

test("constructors validate units", () => {
  assert.throws(() => new Duration({ hours: -1 }), RangeError);
  assert.throws(() => new Duration({ hours: Number.NaN }), RangeError);
  assert.throws(() => new Duration({ hours: Infinity }), RangeError);
  assert.throws(() => new Duration({ hours: "1" }), RangeError);
  assert.throws(() => new Duration({ fortnights: 1 }), TypeError);
  assert.throws(() => new Duration({ sign: 0, days: 1 }), RangeError);
  assert.throws(() => new Duration({ weeks: 1, days: 1 }), RangeError);
  assert.throws(() => new Duration(null), TypeError);
});

test("negate flips the sign but leaves zero positive", () => {
  assert.equal(formatDuration(parseDuration("P1D").negate()), "-P1D");
  assert.equal(formatDuration(parseDuration("-P1D").negate()), "P1D");
  assert.equal(formatDuration(parseDuration("PT0S").negate()), "PT0S");
});

test("equals compares unit by unit", () => {
  assert.equal(parseDuration("PT60M").equals(parseDuration("PT60M")), true);
  assert.equal(parseDuration("PT60M").equals(parseDuration("PT1H")), false);
  assert.equal(parseDuration("P1D").equals({ days: 1 }), true);
  assert.equal(parseDuration("P1D").equals("-P1D"), false);
});

test("humanizes with long labels and correct pluralisation", () => {
  assert.equal(
    humanizeDuration("P1Y2M3DT4H5M6S"),
    "1 year, 2 months, 3 days, 4 hours, 5 minutes, 6 seconds",
  );
  assert.equal(humanizeDuration("PT1H"), "1 hour");
  assert.equal(humanizeDuration("PT2H"), "2 hours");
  assert.equal(humanizeDuration("PT0S"), "0 seconds");
});

test("humanizes with short and narrow labels", () => {
  assert.equal(humanizeDuration("P1Y2M", { style: "short" }), "1 yr 2 mos");
  assert.equal(humanizeDuration("PT1H30M", { style: "short" }), "1 hr 30 mins");
  assert.equal(humanizeDuration("P1Y2M3D", { style: "narrow" }), "1y 2mo 3d");
  assert.equal(humanizeDuration("PT0S", { style: "narrow" }), "0s");
});

test("humanize maxUnits keeps only the leading units", () => {
  assert.equal(humanizeDuration("P1DT12H", { maxUnits: 1 }), "1 day");
  assert.equal(humanizeDuration("P1Y2M3D", { maxUnits: 2 }), "1 year, 2 months");
  assert.equal(humanizeDuration("PT5M", { maxUnits: 3 }), "5 minutes");
});

test("humanize marks negative durations and honours custom separators", () => {
  assert.equal(humanizeDuration("-PT2H30M"), "-2 hours, 30 minutes");
  assert.equal(humanizeDuration("PT2H30M", { separator: " and " }), "2 hours and 30 minutes");
  assert.equal(humanizeDuration("PT0S", { zero: "nothing" }), "nothing");
});

test("humanize validates its options", () => {
  assert.throws(() => humanizeDuration("PT1S", { style: "shouty" }), RangeError);
  assert.throws(() => humanizeDuration("PT1S", { maxUnits: 0 }), RangeError);
  assert.throws(() => humanizeDuration("PT1S", { maxUnits: 1.5 }), RangeError);
  assert.throws(() => humanizeDuration("PT1S", { separator: 5 }), TypeError);
  assert.throws(() => humanizeDuration("PT1S", { zero: 0 }), TypeError);
  assert.throws(() => humanizeDuration("PT1S", "long"), TypeError);
});

test("toMilliseconds is exact for clock units", () => {
  assert.equal(toMilliseconds("PT1S"), MS_SECOND);
  assert.equal(toMilliseconds("PT90M"), 90 * MS_MINUTE);
  assert.equal(toMilliseconds("P2D"), 2 * MS_DAY);
  assert.equal(toMilliseconds("P1W"), 7 * MS_DAY);
  assert.equal(toMilliseconds("-PT1H"), -MS_HOUR);
  assert.equal(toMilliseconds("PT0S"), 0);
});

test("toMilliseconds approximates months and years by their means", () => {
  const year = toMilliseconds("P1Y");
  const month = toMilliseconds("P1M");
  assert.equal(year, 365.2425 * MS_DAY);
  assert.equal(month, year / 12);
  assert.ok(month > 30 * MS_DAY && month < 31 * MS_DAY);
});

test("fromMilliseconds decomposes into clock units", () => {
  const value = fromMilliseconds(MS_DAY + MS_HOUR + MS_MINUTE + MS_SECOND);
  assert.deepEqual(value.toObject(), {
    sign: 1,
    years: 0,
    months: 0,
    weeks: 0,
    days: 1,
    hours: 1,
    minutes: 1,
    seconds: 1,
  });
  assert.equal(fromMilliseconds(-1500).sign, -1);
  assert.equal(fromMilliseconds(-1500).seconds, 1.5);
  assert.equal(formatDuration(fromMilliseconds(0)), "PT0S");
  assert.throws(() => fromMilliseconds(Number.NaN), TypeError);
  assert.throws(() => fromMilliseconds("1000"), TypeError);
});

test("addDuration walks months on the calendar and clamps the day", () => {
  const jan31 = new Date("2024-01-31T00:00:00.000Z");
  assert.equal(addDuration(jan31, "P1M").toISOString(), "2024-02-29T00:00:00.000Z");
  assert.equal(
    addDuration(new Date("2023-01-31T00:00:00.000Z"), "P1M").toISOString(),
    "2023-02-28T00:00:00.000Z",
  );
  assert.equal(
    addDuration(new Date("2024-03-31T00:00:00.000Z"), "-P1M").toISOString(),
    "2024-02-29T00:00:00.000Z",
  );
});

test("addDuration handles years, including leap days", () => {
  assert.equal(
    addDuration(new Date("2024-02-29T12:00:00.000Z"), "P1Y").toISOString(),
    "2025-02-28T12:00:00.000Z",
  );
  assert.equal(
    addDuration(new Date("2024-02-29T12:00:00.000Z"), "P4Y").toISOString(),
    "2028-02-29T12:00:00.000Z",
  );
  assert.equal(
    addDuration(new Date("2024-01-15T00:00:00.000Z"), "-P2Y").toISOString(),
    "2022-01-15T00:00:00.000Z",
  );
});

test("addDuration adds weeks, days and clock units exactly", () => {
  const start = new Date("2024-06-10T08:30:00.000Z");
  assert.equal(addDuration(start, "P2W").toISOString(), "2024-06-24T08:30:00.000Z");
  assert.equal(addDuration(start, "P3D").toISOString(), "2024-06-13T08:30:00.000Z");
  assert.equal(addDuration(start, "PT90M").toISOString(), "2024-06-10T10:00:00.000Z");
  assert.equal(addDuration(start, "-PT8H30M").toISOString(), "2024-06-10T00:00:00.000Z");
});

test("addDuration combines calendar and clock parts", () => {
  const start = new Date("2024-01-31T23:00:00.000Z");
  assert.equal(addDuration(start, "P1MT2H").toISOString(), "2024-03-01T01:00:00.000Z");
});

test("addDuration returns a new Date and never mutates the input", () => {
  const start = new Date("2024-05-01T00:00:00.000Z");
  const result = addDuration(start, "P1D");
  assert.notEqual(result, start);
  assert.equal(start.toISOString(), "2024-05-01T00:00:00.000Z");
  assert.equal(result.toISOString(), "2024-05-02T00:00:00.000Z");
});

test("addDuration of zero keeps the same instant", () => {
  const start = new Date("2024-05-01T06:07:08.900Z");
  assert.equal(addDuration(start, "PT0S").toISOString(), start.toISOString());
});

test("addDuration accepts timestamps, ISO strings and Duration objects", () => {
  const stamp = Date.parse("2024-01-01T00:00:00.000Z");
  assert.equal(addDuration(stamp, Duration.from("P1D")).toISOString(), "2024-01-02T00:00:00.000Z");
  assert.equal(
    addDuration("2024-01-01T00:00:00.000Z", { hours: 12 }).toISOString(),
    "2024-01-01T12:00:00.000Z",
  );
});

test("addDuration rejects invalid dates", () => {
  assert.throws(() => addDuration(new Date("nope"), "P1D"), RangeError);
  assert.throws(() => addDuration("not a date", "P1D"), RangeError);
  assert.throws(() => addDuration(undefined, "P1D"), RangeError);
});

test("addDuration treats fractional years and months as means", () => {
  const start = new Date("2024-01-01T00:00:00.000Z");
  const result = addDuration(start, "P0.5Y");
  const expected = Math.round(start.getTime() + toMilliseconds("P0.5Y"));
  assert.equal(result.getTime(), expected);
});

test("compareDurations orders by approximate length", () => {
  assert.equal(compareDurations("PT1H", "PT60M"), 0);
  assert.equal(compareDurations("PT59M", "PT1H"), -1);
  assert.equal(compareDurations("P2D", "PT36H"), 1);
  assert.equal(compareDurations("-PT1H", "PT1H"), -1);
});

test("toJSON serialises as an ISO 8601 string", () => {
  assert.equal(JSON.stringify({ ttl: parseDuration("PT30M") }), '{"ttl":"PT30M"}');
  assert.equal(String(parseDuration("P1D")), "P1D");
});
