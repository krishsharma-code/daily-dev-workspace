/**
 * url-kit — query strings and URLs, handled carefully.
 *
 * Dependency-free ES module that runs unchanged in the browser and in Node.
 *
 * The built-in `URLSearchParams` is a fine parser but a poor toolkit: it loses
 * repeated keys, cannot express nested params, leaves `!'()*` unencoded, always
 * encodes a space as `+`, and has no way to change one param of a URL without
 * re-serialising (and therefore reformatting) every other param.
 *
 * url-kit keeps the parts you want:
 *
 *  - repeated keys become arrays, and `a[]=1&a[]=2` / `a[b]=1` build nested
 *    arrays and objects;
 *  - strict RFC 3986 percent-encoding (`spaceAsPlus` is opt-in);
 *  - tolerant decoding — a truncated or invalid `%` sequence never throws, it
 *    is passed through literally;
 *  - surgical URL edits: `setQuery` / `removeQuery` touch only the pairs they
 *    own, so unrelated params keep their exact original encoding;
 *  - prototype-pollution guards on every bracket path it builds.
 */

const UNRESERVED = /^[A-Za-z0-9\-._~]$/;
const HEX2 = /^[0-9A-Fa-f]{2}$/;
const DANGEROUS_KEYS = new Set(["__proto__", "constructor", "prototype"]);

/* ------------------------------------------------------------------ */
/* Percent-encoding                                                    */
/* ------------------------------------------------------------------ */

/**
 * Percent-encode a query component per RFC 3986.
 *
 * Only the unreserved set (`A-Z a-z 0-9 - . _ ~`) survives. Everything else is
 * UTF-8 encoded and written as uppercase `%XX`, including `!'()*` — which
 * `encodeURIComponent` leaves raw even though they are sub-delimiters with
 * meaning in a query string.
 *
 * @param {unknown} value
 * @param {{ spaceAsPlus?: boolean }} [options] `spaceAsPlus` writes `+` for a
 *   space (form encoding). Default `false`, i.e. `%20`.
 * @returns {string}
 */
export function encodeQueryComponent(value, options = {}) {
  const { spaceAsPlus = false } = options;
  const input = String(value);
  let out = "";

  // `for…of` walks code points, so astral characters are encoded once, not as
  // two surrogate halves.
  for (const char of input) {
    if (UNRESERVED.test(char)) {
      out += char;
      continue;
    }
    if (char === " " && spaceAsPlus) {
      out += "+";
      continue;
    }
    for (const byte of new TextEncoder().encode(char)) {
      out += "%" + byte.toString(16).toUpperCase().padStart(2, "0");
    }
  }

  return out;
}

/**
 * Decode a query component without ever throwing.
 *
 * `+` becomes a space when `plusAsSpace` is on (the default, matching form
 * encoding), and `%2B` still decodes to a literal `+`. Invalid or truncated
 * sequences such as `%E0%A4` are kept verbatim instead of raising `URIError`.
 *
 * @param {unknown} value
 * @param {{ plusAsSpace?: boolean }} [options]
 * @returns {string}
 */
export function decodeQueryComponent(value, options = {}) {
  const { plusAsSpace = true } = options;
  const input = String(value);
  const spaced = plusAsSpace ? input.replace(/\+/g, " ") : input;
  return tolerantDecode(spaced);
}

function tolerantDecode(input) {
  let out = "";
  let index = 0;

  while (index < input.length) {
    if (input[index] !== "%" || !HEX2.test(input.slice(index + 1, index + 3))) {
      out += input[index];
      index += 1;
      continue;
    }

    const chunks = [];
    while (
      index < input.length &&
      input[index] === "%" &&
      HEX2.test(input.slice(index + 1, index + 3))
    ) {
      chunks.push(input.slice(index, index + 3));
      index += 3;
    }

    out += decodeByteRun(chunks);
  }

  return out;
}

/**
 * Decode a run of `%XX` escapes as UTF-8, byte by byte.
 *
 * A well-formed character is decoded; a byte that starts no valid sequence, or
 * a sequence that is truncated, overlong or a lone surrogate, is left as the
 * literal escape it arrived as. That is why a broken link never throws here.
 */
function decodeByteRun(chunks) {
  const bytes = chunks.map((chunk) => parseInt(chunk.slice(1), 16));
  const minimumFor = [0, 0, 0x80, 0x800, 0x10000];
  let out = "";
  let i = 0;

  while (i < bytes.length) {
    const lead = bytes[i];

    if (lead < 0x80) {
      out += String.fromCharCode(lead);
      i += 1;
      continue;
    }

    const size = lead >= 0xf0 ? 4 : lead >= 0xe0 ? 3 : lead >= 0xc2 ? 2 : 0;
    const tail = bytes.slice(i + 1, i + size);
    const isContinuation = (byte) => byte >= 0x80 && byte <= 0xbf;

    if (
      size === 0 ||
      i + size > bytes.length ||
      !tail.every(isContinuation)
    ) {
      out += chunks[i];
      i += 1;
      continue;
    }

    const codePoint = tail.reduce(
      (acc, byte) => (acc << 6) | (byte & 0x3f),
      lead & (0xff >> (size + 1)),
    );
    const isSurrogate = codePoint >= 0xd800 && codePoint <= 0xdfff;

    if (
      codePoint < minimumFor[size] ||
      isSurrogate ||
      codePoint > 0x10ffff
    ) {
      out += chunks[i];
      i += 1;
      continue;
    }

    out += String.fromCodePoint(codePoint);
    i += size;
  }

  return out;
}

/* ------------------------------------------------------------------ */
/* URL splitting                                                       */
/* ------------------------------------------------------------------ */

function splitUrl(url) {
  const text = String(url);
  const hashIndex = text.indexOf("#");
  const hash = hashIndex === -1 ? "" : text.slice(hashIndex);
  const withoutHash = hashIndex === -1 ? text : text.slice(0, hashIndex);
  const queryIndex = withoutHash.indexOf("?");
  const head = queryIndex === -1 ? withoutHash : withoutHash.slice(0, queryIndex);
  const query = queryIndex === -1 ? "" : withoutHash.slice(queryIndex + 1);
  return { head, query, hash };
}

function joinUrl({ head, query, hash }) {
  return head + (query ? "?" + query : "") + hash;
}

function rawPairs(query) {
  return query ? query.split("&").filter(Boolean) : [];
}

function splitPair(pair) {
  const eq = pair.indexOf("=");
  return eq === -1
    ? { rawKey: pair, rawValue: "" }
    : { rawKey: pair.slice(0, eq), rawValue: pair.slice(eq + 1) };
}

/* ------------------------------------------------------------------ */
/* Parsing                                                             */
/* ------------------------------------------------------------------ */

/**
 * Parse a query string (or a whole URL) into a plain object.
 *
 * Repeated keys become arrays (`duplicates: "array"`, the default) and bracket
 * notation builds nesting: `?tag[]=a&tag[]=b&filter[status]=open` gives
 * `{ tag: ["a", "b"], filter: { status: "open" } }`. Values are always strings.
 *
 * Dangerous keys (`__proto__`, `constructor`, `prototype`) are dropped, so a
 * hostile link cannot pollute `Object.prototype`.
 *
 * @param {string} input Query string with or without a leading `?`; a full URL
 *   is also accepted.
 * @param {{
 *   duplicates?: "array" | "first" | "last",
 *   brackets?: boolean,
 *   plusAsSpace?: boolean,
 *   maxDepth?: number,
 * }} [options]
 * @returns {Record<string, unknown>}
 */
export function parseQuery(input, options = {}) {
  const {
    duplicates = "array",
    brackets = true,
    plusAsSpace = true,
    maxDepth = 6,
  } = options;

  const result = {};
  if (input === null || input === undefined || input === "") return result;

  let query = String(input).trim();
  if (query.includes("#")) query = query.slice(0, query.indexOf("#"));
  if (query.startsWith("?")) query = query.slice(1);
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(query)) {
    query = new URL(query).search.slice(1);
  }
  if (!query) return result;

  for (const pair of rawPairs(query)) {
    const { rawKey, rawValue } = splitPair(pair);
    const key = decodeQueryComponent(rawKey, { plusAsSpace });
    const value = decodeQueryComponent(rawValue, { plusAsSpace });
    if (!key) continue;

    // Guard the root of a bracket path as well as the flat key: `__proto__[x]`
    // and `__proto__` are equally unwelcome.
    if (DANGEROUS_KEYS.has(key.split("[")[0])) continue;

    const path = brackets ? parseKeyPath(key, maxDepth) : [key];
    if (path) {
      assignPath(result, path, value, duplicates);
    } else {
      // A malformed bracket key (`a[b][`) is kept whole rather than dropped.
      setKey(result, key, value, duplicates);
    }
  }

  return result;
}

function parseKeyPath(key, maxDepth) {
  if (!key.includes("[")) return [key];
  const match = /^([^[\]]+)((?:\[[^[\]]*\])+)$/.exec(key);
  if (!match) return null;

  const segments = [
    match[1],
    ...match[2].match(/\[[^[\]]*\]/g).map((part) => {
      const inner = part.slice(1, -1);
      return inner === "" ? "[]" : inner;
    }),
  ];

  if (segments.length > maxDepth) return null;
  if (segments.some((segment) => DANGEROUS_KEYS.has(segment))) return null;
  return segments;
}

function isIndex(segment) {
  return segment !== undefined && /^\d+$/.test(segment);
}

function isArraySlot(segment) {
  return segment === "[]" || isIndex(segment);
}

function setKey(target, key, value, duplicates) {
  if (!target || typeof target !== "object") return;
  const existing = Object.prototype.hasOwnProperty.call(target, key)
    ? target[key]
    : undefined;

  if (existing === undefined) {
    target[key] = value;
    return;
  }
  if (duplicates === "first") return;
  if (duplicates === "last") {
    target[key] = value;
    return;
  }
  if (Array.isArray(existing)) {
    existing.push(value);
    return;
  }
  target[key] = [existing, value];
}

function assignPath(root, segments, value, duplicates) {
  let cursor = root;

  for (let i = 0; i < segments.length; i += 1) {
    const segment = segments[i];
    const isLast = i === segments.length - 1;

    if (isLast) {
      if (segment === "[]") {
        if (Array.isArray(cursor)) cursor.push(value);
        return;
      }
      if (Array.isArray(cursor) && isIndex(segment)) {
        const index = Number(segment);
        if (cursor[index] === undefined || duplicates === "last") {
          cursor[index] = value;
        } else if (duplicates === "array") {
          cursor[index] = Array.isArray(cursor[index])
            ? [...cursor[index], value]
            : [cursor[index], value];
        }
        return;
      }
      setKey(cursor, segment, value, duplicates);
      return;
    }

    const next = segments[i + 1];

    if (segment === "[]") {
      if (!Array.isArray(cursor)) return;
      const child = isArraySlot(next) ? [] : {};
      cursor.push(child);
      cursor = child;
      continue;
    }

    if (Array.isArray(cursor) && isIndex(segment)) {
      const index = Number(segment);
      if (cursor[index] === null || typeof cursor[index] !== "object") {
        cursor[index] = isArraySlot(next) ? [] : {};
      }
      cursor = cursor[index];
      continue;
    }

    if (!cursor || typeof cursor !== "object") return;
    let existing = Object.prototype.hasOwnProperty.call(cursor, segment)
      ? cursor[segment]
      : undefined;
    if (existing === null || typeof existing !== "object") {
      existing = isArraySlot(next) ? [] : {};
      cursor[segment] = existing;
    }
    cursor = existing;
  }
}

/* ------------------------------------------------------------------ */
/* Serialising                                                         */
/* ------------------------------------------------------------------ */

function scalarToString(value) {
  if (value === null || value === undefined) return "";
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function walkParams(value, key, pairs, options) {
  const { arrayFormat = "brackets", skipNull = true } = options;

  if (value === null || value === undefined) {
    if (!skipNull) pairs.push([key, ""]);
    return;
  }
  if (value instanceof Date) {
    pairs.push([key, value.toISOString()]);
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return;
    if (arrayFormat === "comma") {
      pairs.push([key, value.map(scalarToString).join(",")]);
      return;
    }
    value.forEach((item, index) => {
      const childKey =
        arrayFormat === "indices"
          ? `${key}[${index}]`
          : arrayFormat === "repeat"
            ? key
            : `${key}[]`;
      walkParams(item, childKey, pairs, options);
    });
    return;
  }
  if (typeof value === "object") {
    for (const childKey of Object.keys(value)) {
      walkParams(value[childKey], `${key}[${childKey}]`, pairs, options);
    }
    return;
  }
  pairs.push([key, scalarToString(value)]);
}

function encodePairs(pairs, spaceAsPlus) {
  return pairs
    .map(
      ([key, value]) =>
        `${encodeQueryComponent(key, { spaceAsPlus })}=${encodeQueryComponent(value, { spaceAsPlus })}`,
    )
    .join("&");
}

/**
 * Serialise a plain object into a query string (no leading `?`).
 *
 * `null` and `undefined` are skipped, `Date` becomes an ISO string, booleans
 * become `true`/`false`, and `undefined` array members are dropped.
 *
 * @param {Record<string, unknown>} params
 * @param {{
 *   arrayFormat?: "brackets" | "repeat" | "indices" | "comma",
 *   spaceAsPlus?: boolean,
 *   sort?: boolean,
 *   skipNull?: boolean,
 * }} [options]
 * @returns {string}
 */
export function stringifyQuery(params, options = {}) {
  const {
    arrayFormat = "brackets",
    spaceAsPlus = false,
    sort = false,
    skipNull = true,
  } = options;

  const pairs = [];
  if (params && typeof params === "object") {
    for (const key of Object.keys(params)) {
      walkParams(params[key], key, pairs, { arrayFormat, skipNull });
    }
  }

  if (sort) {
    pairs.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  }

  return encodePairs(pairs, spaceAsPlus);
}

/* ------------------------------------------------------------------ */
/* Surgical edits to a URL                                             */
/* ------------------------------------------------------------------ */

/**
 * Read one param from a URL. Returns a string, an array when the key repeats,
 * or `undefined` when it is absent. Bracket children (`a[b]`) are not matched.
 *
 * @param {string} url
 * @param {string} key
 * @param {{ plusAsSpace?: boolean }} [options]
 * @returns {string | string[] | undefined}
 */
export function getQuery(url, key, options = {}) {
  const { plusAsSpace = true } = options;
  const { query } = splitUrl(url);
  const values = [];

  for (const pair of rawPairs(query)) {
    const { rawKey, rawValue } = splitPair(pair);
    if (decodeQueryComponent(rawKey, { plusAsSpace }) !== key) continue;
    values.push(decodeQueryComponent(rawValue, { plusAsSpace }));
  }

  if (values.length === 0) return undefined;
  return values.length === 1 ? values[0] : values;
}

function withoutKey(pairs, key, plusAsSpace) {
  return pairs.filter((pair) => {
    const { rawKey } = splitPair(pair);
    const decoded = decodeQueryComponent(rawKey, { plusAsSpace });
    return decoded !== key && !decoded.startsWith(`${key}[`);
  });
}

/**
 * Set one param on a URL, leaving every other pair byte-for-byte untouched.
 *
 * Existing pairs for `key` (and its bracket children) are replaced. Passing
 * `null` or `undefined` removes the param. Works on relative URLs too.
 *
 * @param {string} url
 * @param {string} key
 * @param {unknown} value
 * @param {{
 *   arrayFormat?: "brackets" | "repeat" | "indices" | "comma",
 *   spaceAsPlus?: boolean,
 *   plusAsSpace?: boolean,
 * }} [options]
 * @returns {string}
 */
export function setQuery(url, key, value, options = {}) {
  const {
    arrayFormat = "brackets",
    spaceAsPlus = false,
    plusAsSpace = true,
  } = options;

  const parts = splitUrl(url);
  const kept = withoutKey(rawPairs(parts.query), key, plusAsSpace);
  const added = [];
  walkParams(value, key, added, { arrayFormat, skipNull: true });

  return joinUrl({
    head: parts.head,
    query: [...kept, ...added.map((pair) => encodePairs([pair], spaceAsPlus))].join("&"),
    hash: parts.hash,
  });
}

/**
 * Remove one param (and its bracket children) from a URL.
 *
 * @param {string} url
 * @param {string} key
 * @param {{ plusAsSpace?: boolean }} [options]
 * @returns {string}
 */
export function removeQuery(url, key, options = {}) {
  const { plusAsSpace = true } = options;
  const parts = splitUrl(url);
  const kept = withoutKey(rawPairs(parts.query), key, plusAsSpace);
  return joinUrl({ head: parts.head, query: kept.join("&"), hash: parts.hash });
}

/**
 * Merge several params into a URL, one `setQuery` per key.
 *
 * @param {string} url
 * @param {Record<string, unknown>} params
 * @param {Parameters<typeof setQuery>[3]} [options]
 * @returns {string}
 */
export function mergeQuery(url, params, options = {}) {
  let next = String(url);
  if (!params || typeof params !== "object") return next;
  for (const key of Object.keys(params)) {
    next = setQuery(next, key, params[key], options);
  }
  return next;
}

/* ------------------------------------------------------------------ */
/* Whole-URL helpers                                                   */
/* ------------------------------------------------------------------ */

/**
 * Parse an absolute URL (or a relative one plus a base) into its parts, with
 * the query already decoded.
 *
 * @param {string} url
 * @param {string} [base] Required when `url` is relative.
 * @returns {{ href: string, origin: string, pathname: string, hash: string,
 *   params: Record<string, unknown> }}
 */
export function parseUrl(url, base) {
  const parsed = new URL(String(url), base);
  return {
    href: parsed.href,
    origin: parsed.origin,
    pathname: parsed.pathname,
    hash: parsed.hash,
    params: parseQuery(parsed.search),
  };
}

/**
 * Resolve `href` against `base`, returning `null` instead of throwing on input
 * a browser would reject.
 *
 * @param {string} href
 * @param {string} [base]
 * @returns {string | null}
 */
export function resolveUrl(href, base) {
  try {
    return new URL(String(href), base).href;
  } catch {
    return null;
  }
}

/**
 * True for anything carrying a scheme, e.g. `https://…`, `mailto:…`, `//cdn`.
 *
 * @param {string} url
 * @returns {boolean}
 */
export function isAbsoluteUrl(url) {
  return /^([a-z][a-z0-9+.-]*:|\/\/)/i.test(String(url).trim());
}

/**
 * Compare two URLs by origin, using `base` for relative ones.
 *
 * @param {string} a
 * @param {string} b
 * @param {string} [base]
 * @returns {boolean}
 */
export function isSameOrigin(a, b, base) {
  try {
    return new URL(String(a), base).origin === new URL(String(b), base).origin;
  } catch {
    return false;
  }
}

/**
 * Canonicalise an absolute URL: optionally sort params, drop the fragment and
 * a bare trailing `?`. Default ports and host case are normalised by `URL`.
 *
 * @param {string} url
 * @param {{
 *   sortParams?: boolean,
 *   stripHash?: boolean,
 *   arrayFormat?: "brackets" | "repeat" | "indices" | "comma",
 * }} [options]
 * @returns {string}
 */
export function normalizeUrl(url, options = {}) {
  const { sortParams = true, stripHash = false, arrayFormat = "brackets" } = options;
  const parsed = new URL(String(url));

  if (sortParams) {
    const sorted = stringifyQuery(parseQuery(parsed.search), { sort: true, arrayFormat });
    parsed.search = sorted ? `?${sorted}` : "";
  }
  if (stripHash) parsed.hash = "";

  return parsed.href;
}
