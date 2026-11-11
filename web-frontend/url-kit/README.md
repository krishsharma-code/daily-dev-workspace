# url-kit

Query strings and URLs, handled carefully: nested and repeated params, strict
RFC 3986 percent-encoding, decoding that never throws, and URL edits that leave
every other pair byte-for-byte alone. Dependency-free, an ES module, and it runs
unchanged in the browser and in Node.

`URLSearchParams` is a decent parser and a poor toolkit. It collapses repeated
keys, cannot express `a[b]=1` or `a[]=1`, leaves `!'()*` raw, always writes a
space as `+`, and gives you no way to change one param without re-serialising —
and therefore reformatting — all the others. url-kit keeps the parts you want.

```js
import { parseQuery, stringifyQuery, setQuery, getQuery } from "./url-kit.js";

parseQuery("?tag[]=a&tag[]=b&filter[status]=open&q=it%27s+fine");
// { tag: ["a", "b"], filter: { status: "open" }, q: "it's fine" }

stringifyQuery({ q: "a b", tag: ["a", "b"] });
// "q=a%20b&tag%5B%5D=a&tag%5B%5D=b"

// Only `page` changes; `keep` keeps its exact original encoding.
setQuery("https://e.com/p?keep=%2Fraw%20value&page=1#top", "page", 2);
// "https://e.com/p?keep=%2Fraw%20value&page=2#top"

getQuery("/x?a=1&a=2", "a"); // ["1", "2"]
```

## API

| Function | Purpose |
| --- | --- |
| `parseQuery(input, options)` | Query string (or full URL) to an object. Options: `duplicates` (`array`/`first`/`last`), `brackets`, `plusAsSpace`, `maxDepth`. |
| `stringifyQuery(params, options)` | Object to a query string, no leading `?`. Options: `arrayFormat` (`brackets`/`repeat`/`indices`/`comma`), `spaceAsPlus`, `sort`, `skipNull`. |
| `encodeQueryComponent(value, options)` | Strict RFC 3986 encoding. `spaceAsPlus` is opt-in. |
| `decodeQueryComponent(value, options)` | Tolerant decoding; `+` is a space unless `plusAsSpace: false`. |
| `getQuery(url, key, options)` | One param from a URL: a string, an array when it repeats, or `undefined`. |
| `setQuery(url, key, value, options)` | Replace just that param (and its bracket children); `null` removes it. |
| `removeQuery(url, key, options)` | Drop every pair for the key. |
| `mergeQuery(url, params, options)` | One `setQuery` per key, in order. |
| `parseUrl(url, base)` | `{ href, origin, pathname, hash, params }` with the query already decoded. |
| `resolveUrl(href, base)` | Resolve a relative reference; `null` instead of a throw. |
| `isAbsoluteUrl(url)` | True for `https:`, `mailto:`, `//cdn…`. |
| `isSameOrigin(a, b, base)` | Compare origins, resolving against a base. |
| `normalizeUrl(url, options)` | Canonical form: sorted params, optional fragment strip. |

## Semantics worth knowing

- **Values are always strings.** `?page=2` gives `"2"`, not `2`. Serialising
  `{ page: 2 }` writes `2`, so a round trip is lossy only for non-string scalars.
- **Repeated keys become arrays.** `?a=1&a=2` is `{ a: ["1", "2"] }` under the
  default `duplicates: "array"`; a single value stays a scalar. Use `"first"` or
  `"last"` when only one value matters.
- **`a[]` and `a[b]` nest.** `tag[]=a&tag[]=b` is an array, `filter[status]=open`
  is an object, `rows[][id]=1` is an array of objects. Nesting is capped at
  `maxDepth` (6 by default); a deeper or malformed bracket key is kept whole as a
  literal key rather than silently dropped.
- **Encoding is strict.** Only `A-Z a-z 0-9 - . _ ~` survive unescaped, so
  `!'()*` become `%21%27%28%29%2A`. A space is `%20`; pass `spaceAsPlus: true`
  for form-style `+`. Astral characters are encoded once, as their UTF-8 bytes.
- **Decoding is tolerant.** `%`, `%zz`, `%E0%A4` and `%FF%FE` are returned
  literally instead of raising `URIError`. Escapes are decoded byte by byte, so
  `%41%E0%A4%42` gives `A%E0%A4B` — the valid `A` and `B` decode, the broken
  sequence survives as text.
- **URL edits are surgical.** `setQuery` and `removeQuery` operate on the raw
  query string: untouched pairs keep their original encoding, order, and any
  `%2F`-vs-`/` choices someone else made. They work on relative URLs and leave
  the path and fragment alone.

## Security

Bracket paths are built only into plain objects, and any key whose root is
`__proto__`, `constructor` or `prototype` is dropped outright. A hostile link
such as `?__proto__[polluted]=yes` therefore cannot reach `Object.prototype`:

```js
parseQuery("__proto__[polluted]=yes&a=1"); // { a: "1" }
({}).polluted;                             // undefined
```

`maxDepth` bounds how deep a link can make you allocate.

## Run it

Open `index.html` in a browser — it is a live playground over this module, with
no build step and no dependencies. Serving the folder (`python3 -m http.server`)
is only needed if your browser blocks module imports over `file://`.

```sh
npm test        # node --test, 38 tests, no dependencies
```

## Files

```
url-kit/
├── README.md
├── index.html        # live playground
├── package.json
├── url-kit.css       # styles for the demo only
├── url-kit.js        # the module
└── url-kit.test.mjs  # node:test suite
```
