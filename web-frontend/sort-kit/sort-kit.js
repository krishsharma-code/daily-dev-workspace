/**
 * sort-kit — dependency-free, locale-aware comparators and a stable multi-key
 * sorter for tables, lists and record sets.
 *
 * The fiddly parts of real sorting are covered rather than left to the caller:
 *
 * - **Multi-key sorting** with a per-key direction, so "role ascending, then
 *   salary descending" is one call.
 * - **Blank handling that ignores direction.** Missing values go last (or
 *   first) whether the key is ascending or descending — reversing a column
 *   must not drag every empty cell to the top.
 * - **Natural ordering** for strings, so `item9` sorts before `item10`.
 * - **A total order for mixed data.** Numbers, booleans, dates and strings get
 *   a fixed rank, so a column with stray types never produces an
 *   order-dependent mess.
 * - **Stability.** Equal rows keep their original relative order, so sorting
 *   by one column and then another behaves like a spreadsheet.
 *
 * Nothing is mutated and nothing is cloned: `sortBy` returns a new array of
 * your original items.
 *
 * Runs unchanged in the browser and in Node — no DOM, no build step.
 */

/** Ranks used to order values of different types against each other. */
const RANK = {
  boolean: 0,
  number: 1,
  date: 2,
  string: 3,
  other: 4,
};

const DIRECTIONS = new Set(["asc", "desc"]);
const NULL_PLACEMENTS = new Set(["first", "last"]);
const KEY_TYPES = new Set(["auto", "string", "natural", "number"]);

/** Collators are expensive to build, so identical configurations share one. */
const collatorCache = new Map();

function describe(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return `"${value}"`;
  if (typeof value === "function") return "a function";
  if (Array.isArray(value)) return "an array";
  if (typeof value === "object") return "an object";
  return String(value);
}

/**
 * A number that is `NaN` cannot be ordered, so it is treated as missing.
 */
function isNullish(value) {
  return (
    value === null ||
    value === undefined ||
    (typeof value === "number" && Number.isNaN(value))
  );
}

function rankOf(value) {
  switch (typeof value) {
    case "boolean":
      return RANK.boolean;
    case "number":
    case "bigint":
      return RANK.number;
    case "string":
      return RANK.string;
    default:
      break;
  }
  if (value instanceof Date) return RANK.date;
  return RANK.other;
}

/**
 * Build (or reuse) an `Intl.Collator` from a small options bag.
 *
 * @param {object} [options]
 * @param {string|string[]} [options.locale] BCP 47 tag(s). Defaults to the runtime default.
 * @param {boolean} [options.numeric=false] Compare digit runs by value (`item9` before `item10`).
 * @param {string} [options.sensitivity="variant"] ICU sensitivity: `base`, `accent`, `case`, `variant`.
 * @param {string} [options.caseFirst] `upper`, `lower` or `false`.
 * @returns {Intl.Collator}
 */
export function createCollator(options = {}) {
  const { locale, numeric = false, sensitivity = "variant", caseFirst } = options;

  const collatorOptions = { numeric: numeric === true, sensitivity };
  if (caseFirst !== undefined) collatorOptions.caseFirst = caseFirst;
  if (locale !== undefined && locale !== null) collatorOptions.locale = locale;

  const cacheKey = JSON.stringify([
    collatorOptions.locale ?? null,
    collatorOptions.numeric,
    collatorOptions.sensitivity,
    collatorOptions.caseFirst ?? null,
  ]);

  const cached = collatorCache.get(cacheKey);
  if (cached) return cached;

  let collator;
  try {
    collator = new Intl.Collator(collatorOptions.locale, collatorOptions);
  } catch (error) {
    throw new RangeError(
      `invalid collator configuration ${cacheKey}: ${error.message}`,
    );
  }

  collatorCache.set(cacheKey, collator);
  return collator;
}

/**
 * Compare two already-normalised, non-missing values under a collator.
 * Values of different types are ordered by a fixed type rank.
 */
function comparePresent(a, b, collator) {
  const rankA = rankOf(a);
  const rankB = rankOf(b);
  if (rankA !== rankB) return rankA - rankB;

  switch (rankA) {
    case RANK.boolean:
      return (a ? 1 : 0) - (b ? 1 : 0);
    case RANK.number:
      // `<` and `>` also order mixed number/bigint pairs correctly.
      if (a < b) return -1;
      if (a > b) return 1;
      return 0;
    case RANK.date: {
      const ta = a.getTime();
      const tb = b.getTime();
      // Invalid dates read as NaN; treat them as equal to each other.
      if (Number.isNaN(ta) || Number.isNaN(tb)) return 0;
      if (ta < tb) return -1;
      if (ta > tb) return 1;
      return 0;
    }
    default:
      return collator.compare(String(a), String(b));
  }
}

/**
 * Compare two scalar values, with missing values ordered last (or first).
 *
 * @param {*} a
 * @param {*} b
 * @param {object} [options] Collator options, plus `nulls` (`"last"` by default).
 * @returns {number} negative, zero or positive
 */
export function compareValues(a, b, options = {}) {
  const { nulls = "last", ...collatorOptions } = options;
  if (!NULL_PLACEMENTS.has(nulls)) {
    throw new RangeError(`nulls must be "first" or "last", received ${describe(nulls)}`);
  }
  const collator = createCollator(collatorOptions);

  const aMissing = isNullish(a);
  const bMissing = isNullish(b);
  if (aMissing || bMissing) {
    if (aMissing && bMissing) return 0;
    const missingRank = nulls === "first" ? -1 : 1;
    return aMissing ? missingRank : -missingRank;
  }

  return comparePresent(a, b, collator);
}

/**
 * Compare two strings in natural order: digit runs compare by value, so
 * `report9.txt` sorts before `report10.txt`.
 *
 * Non-strings are stringified first, so mixed input still has a total order.
 *
 * @param {*} a
 * @param {*} b
 * @param {object} [options] `locale`, `sensitivity`, `caseFirst`, `nulls`.
 * @returns {number}
 */
export function compareNatural(a, b, options = {}) {
  const { nulls = "last", ...rest } = options;
  if (!NULL_PLACEMENTS.has(nulls)) {
    throw new RangeError(`nulls must be "first" or "last", received ${describe(nulls)}`);
  }

  const aMissing = isNullish(a);
  const bMissing = isNullish(b);
  if (aMissing || bMissing) {
    if (aMissing && bMissing) return 0;
    const missingRank = nulls === "first" ? -1 : 1;
    return aMissing ? missingRank : -missingRank;
  }

  const collator = createCollator({ ...rest, numeric: true });
  const sa = String(a);
  const sb = String(b);
  const result = collator.compare(sa, sb);
  if (result !== 0) return result;

  // The collator treats `007` and `7` as equal; prefer the shorter form so the
  // order is still total and repeatable.
  if (sa.length !== sb.length) return sa.length - sb.length;
  return sa < sb ? -1 : sa > sb ? 1 : 0;
}

/** Read `item[path]`, following a dotted path such as `"user.name"`. */
function readPath(item, path) {
  if (item === null || item === undefined) return undefined;
  if (!path.includes(".")) return item[path];

  let current = item;
  for (const segment of path.split(".")) {
    if (current === null || current === undefined) return undefined;
    current = current[segment];
  }
  return current;
}

function normaliseKey(entry, defaults, position) {
  let get;
  let direction = defaults.direction;
  let nulls = defaults.nulls;
  let type = defaults.type;

  if (typeof entry === "string") {
    if (entry === "") {
      throw new TypeError(`key at position ${position} must not be an empty string`);
    }
    get = (item) => readPath(item, entry);
  } else if (typeof entry === "function") {
    get = entry;
  } else if (entry !== null && typeof entry === "object" && !Array.isArray(entry)) {
    const { key, by, direction: entryDirection, nulls: entryNulls, type: entryType } = entry;

    if (key !== undefined && by !== undefined) {
      throw new TypeError(`key at position ${position} sets both "key" and "by"`);
    }
    if (key === undefined && by === undefined) {
      throw new TypeError(`key at position ${position} needs a "key" or "by"`);
    }

    if (by !== undefined) {
      if (typeof by !== "function") {
        throw new TypeError(
          `key at position ${position} has a "by" that is not a function: ${describe(by)}`,
        );
      }
      get = by;
    } else {
      get = normaliseKey(key, defaults, position).get;
    }

    if (entryDirection !== undefined) direction = entryDirection;
    if (entryNulls !== undefined) nulls = entryNulls;
    if (entryType !== undefined) type = entryType;
  } else {
    throw new TypeError(
      `key at position ${position} must be a string, a function or a descriptor, received ${describe(entry)}`,
    );
  }

  if (!DIRECTIONS.has(direction)) {
    throw new RangeError(
      `key at position ${position} has direction ${describe(direction)}; expected "asc" or "desc"`,
    );
  }
  if (!NULL_PLACEMENTS.has(nulls)) {
    throw new RangeError(
      `key at position ${position} has nulls ${describe(nulls)}; expected "first" or "last"`,
    );
  }
  if (!KEY_TYPES.has(type)) {
    throw new RangeError(
      `key at position ${position} has type ${describe(type)}; expected "auto", "string", "natural" or "number"`,
    );
  }

  return { get, direction, nulls, type };
}

/** Convert a value to the requested key type, mapping unparsable input to NaN. */
function coerce(value, type) {
  if (isNullish(value)) return value;
  switch (type) {
    case "string":
      return String(value);
    case "natural":
      return String(value);
    case "number": {
      if (typeof value === "number") return value;
      if (typeof value === "bigint") return Number(value);
      if (typeof value === "boolean") return value ? 1 : 0;
      // An empty cell is blank, not zero.
      const text = String(value).trim();
      if (text === "") return NaN;
      const parsed = Number(text);
      return Number.isNaN(parsed) ? NaN : parsed;
    }
    default:
      return value;
  }
}

function makeKeyComparator(key, defaults) {
  const { direction, nulls, type } = key;
  const numeric = type === "natural" || (type === "auto" && defaults.numeric);
  const collator = createCollator({
    locale: defaults.locale,
    sensitivity: defaults.sensitivity,
    caseFirst: defaults.caseFirst,
    numeric,
  });

  return (a, b) => {
    const left = coerce(key.get(a), type);
    const right = coerce(key.get(b), type);

    const leftMissing = isNullish(left);
    const rightMissing = isNullish(right);
    if (leftMissing || rightMissing) {
      if (leftMissing && rightMissing) return 0;
      // Blank placement is absolute: reversing the key does not move blanks.
      const missingRank = nulls === "first" ? -1 : 1;
      return leftMissing ? missingRank : -missingRank;
    }

    const result = comparePresent(left, right, collator);
    return direction === "desc" ? -result : result;
  };
}

function normaliseKeys(keys) {
  if (keys === undefined) {
    throw new TypeError("keys are required: pass a key, an array of keys, or [] to compare items directly");
  }
  if (Array.isArray(keys)) {
    if (keys.length === 0) {
      throw new RangeError("keys must not be empty");
    }
    return keys;
  }
  return [keys];
}

function normaliseDefaults(options) {
  const {
    direction = "asc",
    nulls = "last",
    type = "auto",
    numeric = false,
    locale,
    sensitivity = "variant",
    caseFirst,
  } = options ?? {};

  if (
    options !== undefined &&
    options !== null &&
    (typeof options !== "object" || Array.isArray(options))
  ) {
    throw new TypeError(`options must be an object, received ${describe(options)}`);
  }
  if (!DIRECTIONS.has(direction)) {
    throw new RangeError(`direction must be "asc" or "desc", received ${describe(direction)}`);
  }
  if (!NULL_PLACEMENTS.has(nulls)) {
    throw new RangeError(`nulls must be "first" or "last", received ${describe(nulls)}`);
  }
  if (!KEY_TYPES.has(type)) {
    throw new RangeError(
      `type must be "auto", "string", "natural" or "number", received ${describe(type)}`,
    );
  }
  if (typeof numeric !== "boolean") {
    throw new TypeError(`numeric must be a boolean, received ${describe(numeric)}`);
  }

  return { direction, nulls, type, numeric, locale, sensitivity, caseFirst };
}

/**
 * Build a comparator over whole items.
 *
 * Keys are tried in order; the first that separates two items decides. When
 * every key ties, the comparator returns `0` — `sortBy` is what breaks those
 * ties by original position.
 *
 * @param {string|Function|object|Array} keys A key, or an array of keys.
 *   A key is a property path (`"salary"`, `"user.name"`), an accessor
 *   function, or a descriptor `{ key | by, direction, nulls, type }`.
 * @param {object} [options] Defaults for every key: `direction`, `nulls`,
 *   `type`, `numeric`, `locale`, `sensitivity`, `caseFirst`.
 * @returns {(a: *, b: *) => number}
 */
export function createComparator(keys, options = {}) {
  const defaults = normaliseDefaults(options);
  const normalised = normaliseKeys(keys).map((entry, index) =>
    normaliseKey(entry, defaults, index),
  );
  const comparators = normalised.map((key) => makeKeyComparator(key, defaults));

  return (a, b) => {
    for (const compare of comparators) {
      const result = compare(a, b);
      if (result !== 0) return result;
    }
    return 0;
  };
}

/**
 * Return a sorted copy of `items`. The input array is never mutated, and equal
 * items keep their original relative order (the sort is stable).
 *
 * @param {Array} items
 * @param {string|Function|object|Array} keys See {@link createComparator}.
 * @param {object} [options] See {@link createComparator}.
 * @returns {Array} a new array
 */
export function sortBy(items, keys, options = {}) {
  if (!Array.isArray(items)) {
    throw new TypeError(`items must be an array, received ${describe(items)}`);
  }
  if (items.length < 2) return items.slice();

  const compare = createComparator(keys, options);

  // Decorate with the original index so ties fall back to input order rather
  // than to the engine's (historically unstable) sort.
  const decorated = items.map((item, index) => ({ item, index }));
  decorated.sort((a, b) => compare(a.item, b.item) || a.index - b.index);
  return decorated.map((entry) => entry.item);
}
