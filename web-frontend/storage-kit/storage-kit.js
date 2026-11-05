/**
 * storage-kit — a namespaced, TTL-aware wrapper around any Web Storage backend.
 *
 * Runs unchanged in the browser and in Node: the backend is injected, so the
 * module never touches `window` unless you ask it to use `localStorage`.
 *
 * Entries are stored as a JSON envelope:
 *
 *     { "v": 1, "e": <expiresAt in ms | null>, "d": <value> }
 *
 * Reads are lazy: an entry is checked against the clock only when it is read,
 * listed, or swept. Expired and unreadable entries are deleted on sight, so the
 * store never returns a stale or half-written value.
 */

const ENVELOPE_VERSION = 1;
const MISSING = Symbol("storage-kit:missing");

/**
 * Base error for everything storage-kit throws on its own behalf.
 */
export class StorageKitError extends Error {
  constructor(message, options) {
    super(message);
    this.name = "StorageKitError";
    if (options && options.cause !== undefined) {
      this.cause = options.cause;
    }
  }
}

/**
 * Thrown when a write cannot fit in the backend's quota, even after expired
 * entries have been reclaimed.
 */
export class QuotaExceededError extends StorageKitError {
  constructor(message, options) {
    super(message, options);
    this.name = "QuotaExceededError";
  }
}

/**
 * Thrown when a value cannot be represented as JSON (undefined, functions,
 * symbols, circular structures, BigInt).
 */
export class SerializationError extends StorageKitError {
  constructor(message, options) {
    super(message, options);
    this.name = "SerializationError";
  }
}

function isQuotaError(error) {
  if (!error) return false;
  const name = String(error.name || "");
  if (name === "QuotaExceededError" || name === "NS_ERROR_DOM_QUOTA_REACHED") {
    return true;
  }
  if (error.code === 22 || error.code === 1014) return true;
  return /quota/i.test(String(error.message || ""));
}

function assertStorage(storage) {
  const required = ["getItem", "setItem", "removeItem", "key"];
  const usable =
    storage !== null &&
    typeof storage === "object" &&
    required.every((method) => typeof storage[method] === "function") &&
    typeof storage.length === "number";
  if (!usable) {
    throw new StorageKitError(
      "storage must implement getItem, setItem, removeItem, key and a numeric length",
    );
  }
  return storage;
}

function defaultStorage() {
  try {
    const candidate = globalThis.localStorage;
    if (candidate && typeof candidate.getItem === "function") {
      return assertStorage(candidate);
    }
  } catch (error) {
    throw new StorageKitError(
      "localStorage is not reachable (it can be blocked in private modes); pass a backend via createStore({ storage })",
      { cause: error },
    );
  }
  throw new StorageKitError(
    "No storage backend available here; pass one via createStore({ storage })",
  );
}

function normalizeNamespace(namespace) {
  if (typeof namespace !== "string") {
    throw new TypeError("namespace must be a string");
  }
  const trimmed = namespace.trim();
  if (trimmed === "") {
    throw new RangeError("namespace must not be empty");
  }
  if (trimmed.includes(":")) {
    throw new RangeError(
      'namespace must not contain ":" — the prefix separator would collide with other namespaces',
    );
  }
  return trimmed;
}

function normalizeTTL(value, label) {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new RangeError(
      `${label} must be a positive number of milliseconds, or null for no expiry`,
    );
  }
  return value;
}

function assertKey(key) {
  if (typeof key !== "string" || key === "") {
    throw new TypeError("key must be a non-empty string");
  }
  return key;
}

function encodeEntry(value, expiresAt) {
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch (error) {
    throw new SerializationError(
      `value is not JSON-serialisable: ${error.message}`,
      { cause: error },
    );
  }
  if (encoded === undefined) {
    throw new SerializationError(
      `cannot store a value of type ${typeof value}; use JSON-compatible data`,
    );
  }
  return JSON.stringify({ v: ENVELOPE_VERSION, e: expiresAt, d: value });
}

function decodeEntry(raw) {
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return null;
  }
  if (parsed.v !== ENVELOPE_VERSION) return null;
  if (!Object.prototype.hasOwnProperty.call(parsed, "d")) return null;
  const expiresAt = parsed.e;
  if (expiresAt !== null && !(typeof expiresAt === "number" && Number.isFinite(expiresAt))) {
    return null;
  }
  return { expiresAt, value: parsed.d };
}

/**
 * A single namespaced view over a storage backend.
 *
 * Two stores sharing a backend never see each other's keys, and `clear()` only
 * removes keys belonging to this store.
 */
export class Store {
  #storage;
  #prefix;
  #defaultTTL;
  #clock;

  constructor({ storage, namespace = "storage-kit", defaultTTL = null, now } = {}) {
    this.#storage = assertStorage(storage === undefined ? defaultStorage() : storage);
    this.#prefix = `${normalizeNamespace(namespace)}:`;
    this.#defaultTTL = normalizeTTL(defaultTTL, "defaultTTL");
    if (now !== undefined && typeof now !== "function") {
      throw new TypeError("now must be a function returning a millisecond timestamp");
    }
    this.#clock = now || (() => Date.now());
  }

  /** The namespace this store reads and writes. */
  get namespace() {
    return this.#prefix.slice(0, -1);
  }

  /**
   * Read a value. Returns `fallback` when the key is missing, expired, or its
   * stored payload is unreadable (the bad entry is deleted either way).
   */
  get(key, fallback = null) {
    const entry = this.#read(assertKey(key));
    return entry === null ? fallback : entry.value;
  }

  /** Whether a live (unexpired) entry exists for `key`. */
  has(key) {
    return this.get(assertKey(key), MISSING) !== MISSING;
  }

  /**
   * Write a value. `ttl` overrides the store default for this entry: a positive
   * number of milliseconds, or `null` for "never expires".
   *
   * On a quota failure, expired entries are reclaimed and the write is retried
   * once; a second failure surfaces as `QuotaExceededError`.
   */
  set(key, value, { ttl } = {}) {
    const storageKey = this.#key(assertKey(key));
    const ttlMs = ttl === undefined ? this.#defaultTTL : normalizeTTL(ttl, "ttl");
    const expiresAt = ttlMs === null ? null : this.#clock() + ttlMs;
    const raw = encodeEntry(value, expiresAt);

    try {
      this.#storage.setItem(storageKey, raw);
    } catch (error) {
      if (!isQuotaError(error)) throw error;
      this.sweep();
      try {
        this.#storage.setItem(storageKey, raw);
      } catch (retryError) {
        throw new QuotaExceededError(
          `could not write "${key}": storage quota exhausted (${retryError.message || retryError.name})`,
          { cause: retryError },
        );
      }
    }
    return this;
  }

  /** Remove one entry. Removing a missing key is a no-op. */
  delete(key) {
    this.#storage.removeItem(this.#key(assertKey(key)));
    return this;
  }

  /** Remove every entry in this namespace; other namespaces are untouched. */
  clear() {
    let removed = 0;
    for (const storageKey of this.#ownKeys()) {
      this.#storage.removeItem(storageKey);
      removed += 1;
    }
    return removed;
  }

  /** Live keys in this namespace, sorted. Expired entries are deleted. */
  keys() {
    const now = this.#clock();
    const live = [];
    for (const storageKey of this.#ownKeys()) {
      const raw = this.#storage.getItem(storageKey);
      const entry = raw === null || raw === undefined ? null : decodeEntry(raw);
      if (entry === null || this.#isExpired(entry, now)) {
        this.#storage.removeItem(storageKey);
        continue;
      }
      live.push(storageKey.slice(this.#prefix.length));
    }
    return live.sort();
  }

  /** Number of live entries in this namespace. */
  size() {
    return this.keys().length;
  }

  /** Delete every expired or unreadable entry in this namespace. */
  sweep() {
    const now = this.#clock();
    let removed = 0;
    for (const storageKey of this.#ownKeys()) {
      const raw = this.#storage.getItem(storageKey);
      const entry = raw === null || raw === undefined ? null : decodeEntry(raw);
      if (entry === null || this.#isExpired(entry, now)) {
        this.#storage.removeItem(storageKey);
        removed += 1;
      }
    }
    return removed;
  }

  /**
   * Expiry timestamp for `key`: a number of epoch milliseconds, `null` for a
   * live entry that never expires, or `undefined` when nothing live is stored.
   */
  expiresAt(key) {
    const entry = this.#read(assertKey(key));
    return entry === null ? undefined : entry.expiresAt;
  }

  #key(key) {
    return `${this.#prefix}${key}`;
  }

  #ownKeys() {
    const found = [];
    const total = this.#storage.length;
    for (let index = 0; index < total; index += 1) {
      const storageKey = this.#storage.key(index);
      if (typeof storageKey === "string" && storageKey.startsWith(this.#prefix)) {
        found.push(storageKey);
      }
    }
    return found;
  }

  #isExpired(entry, now) {
    return entry.expiresAt !== null && entry.expiresAt <= now;
  }

  #read(key) {
    const storageKey = this.#key(key);
    const raw = this.#storage.getItem(storageKey);
    if (raw === null || raw === undefined) return null;
    const entry = decodeEntry(raw);
    if (entry === null || this.#isExpired(entry, this.#clock())) {
      this.#storage.removeItem(storageKey);
      return null;
    }
    return entry;
  }
}

/** Create a namespaced store. See `Store` for the options. */
export function createStore(options) {
  return new Store(options);
}

export default createStore;
