/**
 * duration-kit — ISO 8601 durations: parse, format, humanise and apply.
 *
 * A dependency-free ES module that runs unchanged in the browser and in Node.
 * It covers the four things you end up hand-rolling whenever a UI or an API
 * deals in "how long":
 *
 *   parseDuration("P1Y2M3DT4H5M6S")   -> Duration
 *   formatDuration(duration)          -> "P1Y2M3DT4H5M6S"
 *   humanizeDuration(duration)        -> "1 year, 2 months, 3 days, 4 hours, …"
 *   addDuration(date, duration)       -> a new Date, calendar-aware
 *
 * A `Duration` is an immutable bundle of calendar units (years, months, weeks,
 * days) plus clock units (hours, minutes, seconds) and a sign. Units are never
 * silently collapsed into each other, because a month is not 30 days: `PT720H`
 * and `P1M` are different values here, and only `addDuration` decides what a
 * month means — by walking the calendar.
 *
 * No unit ever carries a negative value; the direction lives in `sign`.
 */

const UNIT_ORDER = ["years", "months", "weeks", "days", "hours", "minutes", "seconds"];

const MS_PER_SECOND = 1000;
const MS_PER_MINUTE = 60 * MS_PER_SECOND;
const MS_PER_HOUR = 60 * MS_PER_MINUTE;
const MS_PER_DAY = 24 * MS_PER_HOUR;
const MS_PER_WEEK = 7 * MS_PER_DAY;

// Mean Gregorian lengths, used only for approximations (toMilliseconds) and for
// the fractional years/months that addDuration cannot walk on a calendar.
const DAYS_PER_YEAR = 365.2425;
const MS_PER_YEAR = DAYS_PER_YEAR * MS_PER_DAY;
const MS_PER_MONTH = MS_PER_YEAR / 12;

// P[nY][nM][nW][nD][T[nH][nM][nS]] — every part optional, validated below.
const ISO_PATTERN =
  /^([+-])?P(?:(\d+(?:[.,]\d+)?)Y)?(?:(\d+(?:[.,]\d+)?)M)?(?:(\d+(?:[.,]\d+)?)W)?(?:(\d+(?:[.,]\d+)?)D)?(?:T(?:(\d+(?:[.,]\d+)?)H)?(?:(\d+(?:[.,]\d+)?)M)?(?:(\d+(?:[.,]\d+)?)S)?)?$/;

const LABELS = {
  long: [
    ["year", "years"],
    ["month", "months"],
    ["week", "weeks"],
    ["day", "days"],
    ["hour", "hours"],
    ["minute", "minutes"],
    ["second", "seconds"],
  ],
  short: [
    ["yr", "yrs"],
    ["mo", "mos"],
    ["wk", "wks"],
    ["day", "days"],
    ["hr", "hrs"],
    ["min", "mins"],
    ["sec", "secs"],
  ],
  narrow: [
    ["y", "y"],
    ["mo", "mo"],
    ["w", "w"],
    ["d", "d"],
    ["h", "h"],
    ["m", "m"],
    ["s", "s"],
  ],
};

/** Render a number without binary-float noise ("0.30000000000000004"). */
function formatNumber(value) {
  const rounded = Math.round(value * 1e9) / 1e9;
  return String(rounded);
}

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertDate(value, label) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    throw new RangeError(`${label} must be a valid Date (received ${String(value)})`);
  }
  return date;
}

/**
 * An immutable ISO 8601 duration.
 *
 * Every unit is a non-negative finite number (fractions are allowed — `PT1.5H`
 * is `hours: 1.5`), and `sign` is `1` or `-1`. A duration whose units are all
 * zero always normalises to `sign: 1`, so `-PT0S` cannot exist.
 */
export class Duration {
  #parts;

  constructor(parts = {}) {
    if (!isPlainObject(parts)) {
      throw new TypeError("Duration expects a plain object of units");
    }

    const allowed = new Set([...UNIT_ORDER, "sign"]);
    for (const key of Object.keys(parts)) {
      if (!allowed.has(key)) {
        throw new TypeError(`Unknown duration unit: ${JSON.stringify(key)}`);
      }
    }

    const values = {};
    for (const unit of UNIT_ORDER) {
      const raw = parts[unit] === undefined ? 0 : parts[unit];
      if (typeof raw !== "number" || !Number.isFinite(raw)) {
        throw new RangeError(`Duration ${unit} must be a finite number`);
      }
      if (raw < 0) {
        throw new RangeError(`Duration ${unit} must not be negative; use sign instead`);
      }
      values[unit] = raw;
    }

    if (values.weeks > 0) {
      const others = UNIT_ORDER.filter((unit) => unit !== "weeks" && values[unit] > 0);
      if (others.length > 0) {
        throw new RangeError(
          "ISO 8601 does not allow weeks to be combined with other units",
        );
      }
    }

    const isEmpty = UNIT_ORDER.every((unit) => values[unit] === 0);
    const rawSign = parts.sign === undefined ? 1 : parts.sign;
    if (rawSign !== 1 && rawSign !== -1) {
      throw new RangeError("Duration sign must be 1 or -1");
    }

    this.sign = isEmpty ? 1 : rawSign;
    for (const unit of UNIT_ORDER) {
      this[unit] = values[unit];
    }
    this.#parts = Object.freeze({ ...values });
    Object.freeze(this);
  }

  /** Parse an ISO 8601 duration string. Throws `SyntaxError` when invalid. */
  static parse(text) {
    return parseDuration(text);
  }

  /** Accept a `Duration`, an ISO 8601 string, a plain object of units, or milliseconds. */
  static from(value) {
    if (value instanceof Duration) return value;
    if (typeof value === "string") return parseDuration(value);
    if (typeof value === "number") return fromMilliseconds(value);
    if (isPlainObject(value)) return new Duration(value);
    throw new TypeError(
      "Expected a Duration, an ISO 8601 string, a units object or a number of milliseconds",
    );
  }

  /** Build a duration from a span of milliseconds, using clock units only. */
  static fromMilliseconds(milliseconds) {
    return fromMilliseconds(milliseconds);
  }

  /** True when every unit is zero. */
  get isEmpty() {
    return UNIT_ORDER.every((unit) => this.#parts[unit] === 0);
  }

  /** Units as a fresh, mutable object (safe to serialise or spread). */
  toObject() {
    return { sign: this.sign, ...this.#parts };
  }

  /** Approximate length in milliseconds — see `toMilliseconds` for the caveat. */
  toMilliseconds() {
    return toMilliseconds(this);
  }

  /** The same duration pointing the other way; zero stays zero. */
  negate() {
    return new Duration({ ...this.#parts, sign: -this.sign });
  }

  /** Structural equality (unit by unit, then sign). */
  equals(other) {
    const that = Duration.from(other);
    return (
      this.sign === that.sign && UNIT_ORDER.every((unit) => this[unit] === that[unit])
    );
  }

  /** Canonical ISO 8601 string. */
  toString() {
    return formatDuration(this);
  }

  /** Serialises as the canonical ISO 8601 string. */
  toJSON() {
    return formatDuration(this);
  }
}

/**
 * Parse an ISO 8601 duration such as `P1Y2M3DT4H5M6S`, `P2W`, `-PT30M` or
 * `PT0.5S`.
 *
 * Rules enforced (all per ISO 8601):
 * - at least one component is required, and a `T` must be followed by one;
 * - a decimal fraction is allowed only on the smallest component present;
 * - `W` cannot be combined with any other component;
 * - the sign is either `-` or `+`.
 *
 * @param {string} text
 * @returns {Duration}
 * @throws {SyntaxError} when the string is not a valid ISO 8601 duration
 */
export function parseDuration(text) {
  if (typeof text !== "string") {
    throw new TypeError("parseDuration expects a string");
  }

  const trimmed = text.trim();
  const match = ISO_PATTERN.exec(trimmed);
  if (!match) {
    throw new SyntaxError(`Not an ISO 8601 duration: ${JSON.stringify(text)}`);
  }
  if (/T$/.test(trimmed)) {
    throw new SyntaxError(
      `The time designator must be followed by a component: ${JSON.stringify(text)}`,
    );
  }

  const [, signText, ...groups] = match;
  const values = {};
  let lastIndex = -1;

  groups.forEach((raw, index) => {
    if (raw === undefined) return;
    values[UNIT_ORDER[index]] = Number(raw.replace(",", "."));
    lastIndex = index;
  });

  if (lastIndex === -1) {
    throw new SyntaxError(`A duration needs at least one component: ${JSON.stringify(text)}`);
  }

  groups.forEach((raw, index) => {
    if (raw !== undefined && /[.,]/.test(raw) && index !== lastIndex) {
      throw new SyntaxError(
        `Only the smallest component may carry a fraction: ${JSON.stringify(text)}`,
      );
    }
  });

  if (values.weeks !== undefined && Object.keys(values).length > 1) {
    throw new SyntaxError(
      `ISO 8601 does not allow weeks to be combined with other units: ${JSON.stringify(text)}`,
    );
  }

  return new Duration({ ...values, sign: signText === "-" ? -1 : 1 });
}

/**
 * Render a duration as canonical ISO 8601: zero components are dropped, units
 * keep their given shape (a week stays a week), and an empty duration is
 * `PT0S`.
 *
 * @param {Duration|string|object|number} input
 * @returns {string}
 */
export function formatDuration(input) {
  const duration = Duration.from(input);

  const date = [];
  if (duration.years) date.push(`${formatNumber(duration.years)}Y`);
  if (duration.months) date.push(`${formatNumber(duration.months)}M`);
  if (duration.weeks) date.push(`${formatNumber(duration.weeks)}W`);
  if (duration.days) date.push(`${formatNumber(duration.days)}D`);

  const time = [];
  if (duration.hours) time.push(`${formatNumber(duration.hours)}H`);
  if (duration.minutes) time.push(`${formatNumber(duration.minutes)}M`);
  if (duration.seconds) time.push(`${formatNumber(duration.seconds)}S`);

  let body = date.join("");
  if (time.length > 0) body += `T${time.join("")}`;
  if (body === "") body = "T0S";

  return `${duration.sign < 0 ? "-" : ""}P${body}`;
}

/**
 * Describe a duration in words: `1 year, 2 months, 3 days`.
 *
 * @param {Duration|string|object|number} input
 * @param {object} [options]
 * @param {"long"|"short"|"narrow"} [options.style="long"] unit wording
 * @param {number} [options.maxUnits=Infinity] how many leading units to keep
 * @param {string} [options.separator] joiner between units
 * @param {string} [options.zero] text for an empty duration
 * @returns {string}
 */
export function humanizeDuration(input, options = {}) {
  if (!isPlainObject(options)) {
    throw new TypeError("humanizeDuration options must be an object");
  }

  const duration = Duration.from(input);
  const { style = "long", maxUnits = Infinity, separator, zero } = options;

  const labels = LABELS[style];
  if (!labels) {
    throw new RangeError(`Unknown style ${JSON.stringify(style)}; use long, short or narrow`);
  }
  if (maxUnits !== Infinity && (!Number.isInteger(maxUnits) || maxUnits < 1)) {
    throw new RangeError("maxUnits must be a positive integer or Infinity");
  }
  if (separator !== undefined && typeof separator !== "string") {
    throw new TypeError("separator must be a string");
  }
  if (zero !== undefined && typeof zero !== "string") {
    throw new TypeError("zero must be a string");
  }

  const glue = style === "narrow" ? "" : " ";
  const joiner = separator === undefined ? (style === "long" ? ", " : " ") : separator;

  const pieces = [];
  for (let index = 0; index < UNIT_ORDER.length; index += 1) {
    const value = duration[UNIT_ORDER[index]];
    if (value === 0) continue;
    const [singular, plural] = labels[index];
    pieces.push(`${formatNumber(value)}${glue}${value === 1 ? singular : plural}`);
    if (pieces.length === maxUnits) break;
  }

  if (pieces.length === 0) {
    const [singular, plural] = labels[labels.length - 1];
    return zero === undefined ? `0${glue}${plural === singular ? singular : plural}` : zero;
  }

  return `${duration.sign < 0 ? "-" : ""}${pieces.join(joiner)}`;
}

/**
 * Approximate a duration in milliseconds.
 *
 * Clock units are exact. Days and weeks are exact too (a day is 86 400 000 ms).
 * Years and months are **means** — 365.2425 days and a twelfth of that — because
 * neither has a fixed length. Use `addDuration` when you need the real calendar.
 *
 * @param {Duration|string|object|number} input
 * @returns {number}
 */
export function toMilliseconds(input) {
  const duration = Duration.from(input);
  const magnitude =
    duration.years * MS_PER_YEAR +
    duration.months * MS_PER_MONTH +
    duration.weeks * MS_PER_WEEK +
    duration.days * MS_PER_DAY +
    duration.hours * MS_PER_HOUR +
    duration.minutes * MS_PER_MINUTE +
    duration.seconds * MS_PER_SECOND;

  return duration.sign * magnitude;
}

/**
 * Build a duration from a span of milliseconds, decomposed into clock units
 * (days, hours, minutes, seconds) — never into months or years, which have no
 * fixed length.
 *
 * @param {number} milliseconds
 * @returns {Duration}
 */
export function fromMilliseconds(milliseconds) {
  if (typeof milliseconds !== "number" || !Number.isFinite(milliseconds)) {
    throw new TypeError("fromMilliseconds expects a finite number");
  }

  const sign = milliseconds < 0 ? -1 : 1;
  let remaining = Math.round(Math.abs(milliseconds));

  const days = Math.floor(remaining / MS_PER_DAY);
  remaining -= days * MS_PER_DAY;
  const hours = Math.floor(remaining / MS_PER_HOUR);
  remaining -= hours * MS_PER_HOUR;
  const minutes = Math.floor(remaining / MS_PER_MINUTE);
  remaining -= minutes * MS_PER_MINUTE;

  return new Duration({
    sign,
    days,
    hours,
    minutes,
    seconds: remaining / MS_PER_SECOND,
  });
}

/**
 * Add a duration to a date and return a **new** Date.
 *
 * Years and months are applied on the calendar, in UTC (so the result never
 * depends on the machine's time zone or on daylight-saving shifts), and
 * day-of-month overflow is clamped: 31 January + `P1M` is 28 (or 29) February.
 * Weeks, days, hours, minutes and seconds are then added as exact milliseconds.
 * A fractional year or month falls back to the mean lengths used by
 * `toMilliseconds`.
 *
 * @param {Date|number|string} date
 * @param {Duration|string|object|number} duration
 * @returns {Date}
 */
export function addDuration(date, duration) {
  const start = assertDate(date, "date");
  const value = Duration.from(duration);
  const sign = value.sign;

  const wholeYears = Math.trunc(value.years);
  const wholeMonths = Math.trunc(value.months);
  const totalMonths = sign * (wholeYears * 12 + wholeMonths);

  let time = start.getTime();

  if (totalMonths !== 0) {
    const cursor = new Date(time);
    const day = cursor.getUTCDate();
    cursor.setUTCDate(1);
    cursor.setUTCMonth(cursor.getUTCMonth() + totalMonths);
    const lastDay = new Date(
      Date.UTC(cursor.getUTCFullYear(), cursor.getUTCMonth() + 1, 0),
    ).getUTCDate();
    cursor.setUTCDate(Math.min(day, lastDay));
    time = cursor.getTime();
  }

  const remainder =
    (value.years - wholeYears) * MS_PER_YEAR +
    (value.months - wholeMonths) * MS_PER_MONTH +
    value.weeks * MS_PER_WEEK +
    value.days * MS_PER_DAY +
    value.hours * MS_PER_HOUR +
    value.minutes * MS_PER_MINUTE +
    value.seconds * MS_PER_SECOND;

  return new Date(Math.round(time + sign * remainder));
}

/**
 * Compare two durations by their approximate length: negative when `a` is
 * shorter than `b`, positive when it is longer, `0` when they tie.
 *
 * @param {Duration|string|object|number} a
 * @param {Duration|string|object|number} b
 * @returns {number}
 */
export function compareDurations(a, b) {
  const left = toMilliseconds(a);
  const right = toMilliseconds(b);
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

export default Duration;
