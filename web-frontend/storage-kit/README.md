# storage-kit

A small, dependency-free wrapper around any Web Storage backend (`localStorage`,
`sessionStorage`, or your own object) that adds the three things the raw API
makes you write by hand every time: **namespacing**, **expiry**, and **safe
reads**.

It runs unchanged in the browser and in Node, because the backend is injected —
the module only reaches for `localStorage` when you let it.

```js
import { createStore } from "./storage-kit.js";

const store = createStore({ namespace: "app", defaultTTL: 60_000 });

store.set("session", { userId: 42 }, { ttl: 15 * 60_000 });
store.get("session");            // { userId: 42 }
store.expiresAt("session");      // epoch ms, or null when it never expires
store.get("session", "anon");    // "anon" once the entry has expired
```

## What it does

| Function | Purpose |
| --- | --- |
| `createStore(options)` | Build a namespaced store. `options`: `storage`, `namespace`, `defaultTTL`, `now`. |
| `store.set(key, value, { ttl })` | Write a JSON-compatible value; `ttl` is milliseconds or `null` for no expiry. Returns the store. |
| `store.get(key, fallback = null)` | Read a live value; expired, missing and unreadable entries all yield the fallback. |
| `store.has(key)` | Whether a live entry exists (a stored `null` counts). |
| `store.delete(key)` | Remove one entry; deleting a missing key is a no-op. |
| `store.keys()` / `store.size()` | Live keys, sorted, and how many there are. Expired entries are deleted as they are listed. |
| `store.sweep()` | Delete every expired or unreadable entry in the namespace; returns how many went. |
| `store.clear()` | Remove the whole namespace; returns how many entries went. Other namespaces are untouched. |
| `store.expiresAt(key)` | Deadline in epoch ms, `null` for "never", `undefined` when nothing live is stored. |

Errors: `StorageKitError` (base), `QuotaExceededError` (write did not fit),
`SerializationError` (value cannot be represented as JSON). Invalid arguments
raise the usual `TypeError` / `RangeError`.

## Semantics worth knowing

- **Storage format.** Each entry is one JSON envelope:
  `{"v":1,"e":<expiresAt|null>,"d":<value>}`. Values must survive a JSON round
  trip — `Date` becomes a string, `undefined`/functions/symbols/BigInt and
  circular structures raise `SerializationError`.
- **Lazy expiry.** Nothing runs on a timer. An entry is checked against the
  clock when it is read, listed or swept, and is deleted on sight once
  `expiresAt <= now`. Use `sweep()` on page load if you want storage reclaimed
  even for keys nobody reads again.
- **Safe reads.** A payload that is not valid JSON, is not an envelope, or has a
  future version number is treated as missing: you get the fallback and the
  broken entry is deleted instead of throwing mid-render.
- **Quota recovery.** If `setItem` throws a quota error, the store sweeps
  expired entries and retries the write once. If it still does not fit you get
  `QuotaExceededError` with the backend error as `cause`, and existing entries
  are left intact. Errors that are not quota-related are rethrown untouched.
- **Namespaces** become a `namespace:` key prefix, so two stores on the same
  backend never collide. A namespace may not be empty or contain `:`.
- **Clock injection.** Pass `now: () => fakeTime` to test expiry deterministically.

## Layout

```
web-frontend/storage-kit/
├── README.md
├── storage-kit.js         # implementation (ES module)
├── storage-kit.css        # styles for the demo
├── index.html             # runnable interactive demo
├── package.json           # `npm test` runs the suite
└── storage-kit.test.mjs   # node:test suite
```

## Try the demo

Because it is an ES module, serve the folder over HTTP rather than opening the
file directly (module scripts are blocked on `file://`):

```bash
cd web-frontend/storage-kit
python3 -m http.server 8000
# then open http://localhost:8000/
```

Write an entry with a TTL, watch it count down and vanish from the table, sweep
or clear the namespace, and inspect the raw envelope stored under the hood. The
neighbour panel writes to a second namespace so you can see that `clear()` stays
inside its own namespace.

## Run the tests

```bash
cd web-frontend/storage-kit
node --test
```

No third-party dependencies are required; Node 18+ is enough to run the suite.
The tests drive the store through an in-memory `Storage` double with an
injectable clock, including the quota and corruption paths.

## Use it in a page

```html
<script type="module">
  import { createStore } from "./storage-kit.js";

  // Defaults to localStorage, namespaced to "app".
  const cache = createStore({ namespace: "app", defaultTTL: 5 * 60_000 });

  async function loadProfile(id) {
    const cached = cache.get(`profile:${id}`);
    if (cached) return cached;

    const profile = await fetch(`/api/profiles/${id}`).then((r) => r.json());
    cache.set(`profile:${id}`, profile);   // expires in five minutes
    return profile;
  }

  window.addEventListener("load", () => {
    const removed = cache.sweep();          // reclaim entries nobody reads again
    if (removed) console.debug(`storage-kit: swept ${removed} expired entries`);
  });
</script>
```

In Node (tests, server-side rendering, scripts) pass your own backend:

```js
import { createStore } from "./storage-kit.js";

const map = new Map();
const memory = {
  get length() { return map.size; },
  key: (i) => [...map.keys()][i] ?? null,
  getItem: (k) => (map.has(k) ? map.get(k) : null),
  setItem: (k, v) => void map.set(k, String(v)),
  removeItem: (k) => void map.delete(k),
};

const store = createStore({ storage: memory, namespace: "build-cache" });
store.set("lastRun", { at: Date.now() }, { ttl: 24 * 60 * 60_000 });
```
