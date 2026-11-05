import test from "node:test";
import assert from "node:assert/strict";

import createStore, {
  createStore as namedCreateStore,
  Store,
  StorageKitError,
  QuotaExceededError,
  SerializationError,
} from "./storage-kit.js";

/** Minimal in-memory Storage implementation, with an optional byte quota. */
class MemoryStorage {
  #map = new Map();
  #quota;

  constructor({ quota = Infinity } = {}) {
    this.#quota = quota;
  }

  get length() {
    return this.#map.size;
  }

  key(index) {
    return index >= 0 && index < this.#map.size ? [...this.#map.keys()][index] : null;
  }

  getItem(key) {
    const name = String(key);
    return this.#map.has(name) ? this.#map.get(name) : null;
  }

  setItem(key, value) {
    const name = String(key);
    const text = String(value);
    const existing = this.#map.has(name) ? this.#map.get(name).length : 0;
    if (this.#bytes() - existing + text.length > this.#quota) {
      const error = new Error("The quota has been exceeded.");
      error.name = "QuotaExceededError";
      error.code = 22;
      throw error;
    }
    this.#map.set(name, text);
  }

  removeItem(key) {
    this.#map.delete(String(key));
  }

  clear() {
    this.#map.clear();
  }

  /** Raw storage keys, for asserting on cleanup. */
  rawKeys() {
    return [...this.#map.keys()];
  }

  #bytes() {
    let total = 0;
    for (const value of this.#map.values()) total += value.length;
    return total;
  }
}

function clockAt(start = 1_700_000_000_000) {
  let current = start;
  return {
    now: () => current,
    advance(ms) {
      current += ms;
      return current;
    },
  };
}

test("round-trips JSON-compatible values", () => {
  const store = createStore({ storage: new MemoryStorage(), namespace: "test" });
  const samples = {
    string: "hello",
    empty: "",
    number: 42.5,
    zero: 0,
    boolean: false,
    nothing: null,
    list: [1, "two", { three: 3 }],
    nested: { a: { b: [{ c: true }] }, d: "e" },
  };

  for (const [key, value] of Object.entries(samples)) {
    store.set(key, value);
    assert.deepEqual(store.get(key), value, `key ${key} did not round-trip`);
  }
});

test("set returns the store for chaining", () => {
  const store = createStore({ storage: new MemoryStorage(), namespace: "chain" });
  assert.equal(store.set("a", 1), store);
  assert.equal(store.delete("a"), store);
});

test("get returns the fallback for a missing key", () => {
  const store = createStore({ storage: new MemoryStorage(), namespace: "miss" });
  assert.equal(store.get("nope"), null);
  assert.equal(store.get("nope", "fallback"), "fallback");
  assert.deepEqual(store.get("nope", { default: true }), { default: true });
});

test("stores are isolated by namespace on a shared backend", () => {
  const storage = new MemoryStorage();
  const alpha = createStore({ storage, namespace: "alpha" });
  const beta = createStore({ storage, namespace: "beta" });

  alpha.set("shared", "from-alpha");
  beta.set("shared", "from-beta");

  assert.equal(alpha.get("shared"), "from-alpha");
  assert.equal(beta.get("shared"), "from-beta");
  assert.deepEqual(alpha.keys(), ["shared"]);
  assert.deepEqual(beta.keys(), ["shared"]);
});

test("clear only removes keys in its own namespace", () => {
  const storage = new MemoryStorage();
  const alpha = createStore({ storage, namespace: "alpha" });
  const beta = createStore({ storage, namespace: "beta" });

  alpha.set("one", 1);
  alpha.set("two", 2);
  beta.set("three", 3);
  storage.setItem("unrelated", "kept");

  assert.equal(alpha.clear(), 2);
  assert.equal(alpha.size(), 0);
  assert.equal(beta.get("three"), 3);
  assert.equal(storage.getItem("unrelated"), "kept");
});

test("keys are sorted and size counts live entries", () => {
  const store = createStore({ storage: new MemoryStorage(), namespace: "sorted" });
  store.set("zebra", 1).set("apple", 2).set("mango", 3);

  assert.deepEqual(store.keys(), ["apple", "mango", "zebra"]);
  assert.equal(store.size(), 3);
});

test("entries expire once the ttl has elapsed", () => {
  const clock = clockAt();
  const store = createStore({
    storage: new MemoryStorage(),
    namespace: "ttl",
    now: clock.now,
  });

  store.set("session", { id: 7 }, { ttl: 1_000 });
  assert.deepEqual(store.get("session"), { id: 7 });
  assert.equal(store.expiresAt("session"), 1_700_000_000_000 + 1_000);

  clock.advance(999);
  assert.deepEqual(store.get("session"), { id: 7 }, "entry expires exactly at its deadline");

  clock.advance(1);
  assert.equal(store.get("session"), null);
  assert.equal(store.has("session"), false);
});

test("an expired entry is deleted from the backend when it is read", () => {
  const clock = clockAt();
  const storage = new MemoryStorage();
  const store = createStore({ storage, namespace: "cleanup", now: clock.now });

  store.set("token", "abc", { ttl: 500 });
  assert.deepEqual(storage.rawKeys(), ["cleanup:token"]);

  clock.advance(500);
  assert.equal(store.get("token"), null);
  assert.deepEqual(storage.rawKeys(), []);
});

test("defaultTTL applies to writes and ttl: null opts out", () => {
  const clock = clockAt();
  const store = createStore({
    storage: new MemoryStorage(),
    namespace: "defaults",
    defaultTTL: 60_000,
    now: clock.now,
  });

  store.set("short", "lived");
  store.set("forever", "kept", { ttl: null });

  assert.equal(store.expiresAt("short"), 1_700_000_000_000 + 60_000);
  assert.equal(store.expiresAt("forever"), null);

  clock.advance(60_000);
  assert.equal(store.get("short", "gone"), "gone");
  assert.equal(store.get("forever"), "kept");
});

test("invalid ttl and defaultTTL values are rejected", () => {
  const storage = new MemoryStorage();
  const store = createStore({ storage, namespace: "bad-ttl" });

  for (const ttl of [0, -5, Number.NaN, Number.POSITIVE_INFINITY, "1000", {}]) {
    assert.throws(() => store.set("k", "v", { ttl }), RangeError, `ttl ${String(ttl)}`);
  }
  assert.throws(() => createStore({ storage, namespace: "x", defaultTTL: 0 }), RangeError);
  assert.throws(() => createStore({ storage, namespace: "x", defaultTTL: -1 }), RangeError);
  assert.throws(() => createStore({ storage, namespace: "x", defaultTTL: "soon" }), RangeError);
});

test("has reflects presence, deletion and expiry", () => {
  const clock = clockAt();
  const store = createStore({ storage: new MemoryStorage(), namespace: "has", now: clock.now });

  assert.equal(store.has("flag"), false);
  store.set("flag", null);
  assert.equal(store.has("flag"), true, "a stored null is still a present entry");
  store.delete("flag");
  assert.equal(store.has("flag"), false);

  store.set("temp", 1, { ttl: 100 });
  clock.advance(100);
  assert.equal(store.has("temp"), false);
});

test("delete removes an entry and tolerates missing keys", () => {
  const storage = new MemoryStorage();
  const store = createStore({ storage, namespace: "del" });

  store.set("a", 1);
  store.delete("a");
  assert.equal(store.get("a"), null);
  assert.deepEqual(storage.rawKeys(), []);

  assert.doesNotThrow(() => store.delete("never-existed"));
});

test("unreadable payloads fall back and are discarded", () => {
  const storage = new MemoryStorage();
  const store = createStore({ storage, namespace: "corrupt" });

  const badPayloads = [
    "{not json",
    "42",
    '"a string"',
    "null",
    JSON.stringify({ v: 2, e: null, d: "future version" }),
    JSON.stringify({ v: 1, e: null }),
    JSON.stringify({ v: 1, e: "tomorrow", d: 1 }),
    JSON.stringify([{ v: 1, e: null, d: 1 }]),
  ];

  for (const payload of badPayloads) {
    storage.setItem("corrupt:key", payload);
    assert.equal(store.get("key", "fallback"), "fallback", `payload ${payload}`);
    assert.equal(storage.getItem("corrupt:key"), null, `payload ${payload} was not cleaned up`);
  }
});

test("values that JSON cannot represent raise SerializationError", () => {
  const store = createStore({ storage: new MemoryStorage(), namespace: "serialise" });
  const circular = {};
  circular.self = circular;

  for (const value of [undefined, () => {}, Symbol("s"), 10n, circular]) {
    assert.throws(() => store.set("k", value), SerializationError);
  }
  assert.equal(store.has("k"), false);
});

test("expiresAt reports the deadline, null, or undefined", () => {
  const clock = clockAt();
  const store = createStore({ storage: new MemoryStorage(), namespace: "meta", now: clock.now });

  assert.equal(store.expiresAt("absent"), undefined);
  store.set("timed", 1, { ttl: 5_000 });
  store.set("open", 2, { ttl: null });

  assert.equal(store.expiresAt("timed"), 1_700_000_000_000 + 5_000);
  assert.equal(store.expiresAt("open"), null);

  clock.advance(5_000);
  assert.equal(store.expiresAt("timed"), undefined);
});

test("keys and size drop expired entries and clean them up", () => {
  const clock = clockAt();
  const storage = new MemoryStorage();
  const store = createStore({ storage, namespace: "listing", now: clock.now });

  store.set("fresh", 1, { ttl: 10_000 });
  store.set("stale", 2, { ttl: 1_000 });

  clock.advance(1_000);
  assert.deepEqual(store.keys(), ["fresh"]);
  assert.equal(store.size(), 1);
  assert.deepEqual(storage.rawKeys(), ["listing:fresh"]);
});

test("sweep reclaims expired and unreadable entries only", () => {
  const clock = clockAt();
  const storage = new MemoryStorage();
  const store = createStore({ storage, namespace: "sweep", now: clock.now });

  store.set("live", 1, { ttl: 10_000 });
  store.set("dead", 2, { ttl: 1_000 });
  storage.setItem("sweep:broken", "not-an-envelope");
  storage.setItem("other:broken", "not-an-envelope");

  clock.advance(1_000);
  assert.equal(store.sweep(), 2);
  assert.deepEqual(storage.rawKeys().sort(), ["other:broken", "sweep:live"]);
  assert.equal(store.sweep(), 0, "sweeping again finds nothing to remove");
});

test("a quota failure reclaims expired entries and retries the write", () => {
  const clock = clockAt();
  const storage = new MemoryStorage({ quota: 300 });
  const store = createStore({ storage, namespace: "quota", now: clock.now });

  store.set("old", "x".repeat(150), { ttl: 1_000 });
  clock.advance(1_000);

  // The expired entry still occupies space, but set() should free it and retry.
  store.set("fresh", "y".repeat(150));
  assert.equal(store.get("fresh"), "y".repeat(150));
  assert.deepEqual(storage.rawKeys(), ["quota:fresh"]);
});

test("a quota failure with nothing to reclaim raises QuotaExceededError", () => {
  const storage = new MemoryStorage({ quota: 200 });
  const store = createStore({ storage, namespace: "full" });

  store.set("kept", "k".repeat(100));
  assert.throws(
    () => store.set("too-big", "z".repeat(400)),
    (error) => {
      assert.ok(error instanceof QuotaExceededError);
      assert.ok(error instanceof StorageKitError);
      assert.equal(error.name, "QuotaExceededError");
      assert.match(error.message, /quota/i);
      assert.ok(error.cause, "the backend error is kept as the cause");
      return true;
    },
  );
  assert.equal(store.get("kept"), "k".repeat(100), "existing entries survive the failed write");
});

test("non-quota backend errors are rethrown untouched", () => {
  const storage = new MemoryStorage();
  const store = createStore({ storage, namespace: "boom" });
  const failure = new Error("disk on fire");
  storage.setItem = () => {
    throw failure;
  };

  assert.throws(() => store.set("k", "v"), (error) => error === failure);
});

test("invalid namespace, key, clock and backend are rejected", () => {
  const storage = new MemoryStorage();

  assert.throws(() => createStore({ storage, namespace: "" }), RangeError);
  assert.throws(() => createStore({ storage, namespace: "   " }), RangeError);
  assert.throws(() => createStore({ storage, namespace: "a:b" }), RangeError);
  assert.throws(() => createStore({ storage, namespace: 42 }), TypeError);
  assert.throws(() => createStore({ storage, namespace: "ok", now: 12 }), TypeError);
  assert.throws(() => createStore({ storage: {}, namespace: "ok" }), StorageKitError);
  assert.throws(() => createStore({ storage: null, namespace: "ok" }), StorageKitError);

  const store = createStore({ storage, namespace: "keys" });
  for (const key of ["", 7, null, undefined]) {
    assert.throws(() => store.get(key), TypeError);
    assert.throws(() => store.set(key, 1), TypeError);
    assert.throws(() => store.delete(key), TypeError);
  }
});

test("namespace getter reports the trimmed namespace", () => {
  const store = createStore({ storage: new MemoryStorage(), namespace: "  my-app  " });
  assert.equal(store.namespace, "my-app");
  store.set("k", "v");
  assert.equal(store.get("k"), "v");
});

test("the default export and the named factory are the same function", () => {
  const store = createStore({ storage: new MemoryStorage(), namespace: "ctor" });
  assert.ok(store instanceof Store);
  assert.equal(createStore, namedCreateStore);
});
