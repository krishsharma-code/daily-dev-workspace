# keyboard-shortcuts

A small, dependency-free shortcut registry for the browser. Register a spec such
as `"Mod+K"`, `"?"` or `"g d"`, feed it keydown-like objects, and it tells you
whether a binding fired and whether you should call `preventDefault()`.

It does not touch the DOM, so the same file runs in the browser and in Node —
which is how the tests drive it. No build step, no dependencies.

## Features

- **Single chords** — `"Mod+K"`, `"Ctrl+Shift+Backspace"`, `"Alt+ArrowUp"`.
- **Sequences** — `"g d"` means press `g`, then `d`, within a timeout.
- **`Mod`** resolves to `Cmd` on macOS and `Ctrl` everywhere else.
- **Scopes** with a stack, so a modal can add `j`/`k` without losing global ones.
- **`when` predicates**, e.g. suppress single-key shortcuts while typing.
- **Duplicate detection** and a `conflicts()` report.
- **Platform-aware labels** via `format()`, for building a cheat sheet.

## Quick start

```js
import { createShortcuts, isEditableTarget } from "./keyboard-shortcuts.js";

const shortcuts = createShortcuts({ platform: "auto", sequenceTimeout: 900 });

shortcuts.register("Mod+K", () => openSearch(), { description: "Open search" });
shortcuts.register("g d", () => go("dashboard"), { description: "Go to dashboard" });
shortcuts.register("j", () => next(), { scope: "list", when: (e) => !isEditableTarget(e.target) });

document.addEventListener("keydown", (event) => {
  const result = shortcuts.handle(event);
  if (result.matched && result.preventDefault) event.preventDefault();
});
```

`handle()` returns a plain object:

```js
{ matched: true,  shortcut: { id: 1, spec: "ctrl+k", description: "Open search" },
  pending: false, preventDefault: true }

{ matched: false, shortcut: null, pending: true, buffer: ["g"] }   // sequence waiting
{ matched: false, shortcut: null, pending: false, reason: "no-match" }
```

`reason` is one of `no-match`, `no-candidates`, `repeat`, `when`, `expired`,
`disabled`, `unusable-event`.

## Spec grammar

A spec is one or more **chords** separated by whitespace. Each chord is
modifiers joined to a key with `+`.

| Part | Accepted |
| --- | --- |
| Modifiers | `Mod`, `Ctrl`/`Control`, `Alt`/`Opt`/`Option`, `Shift`, `Meta`/`Cmd`/`Command`/`Super`/`Win` |
| Letters, digits | `a`–`z`, `0`–`9` |
| Symbols | the literal character, e.g. `?`, `/`, `,`; `+` is written `Plus` or as a trailing `++` (`Ctrl++`) |
| Named keys | `Enter`/`Return`, `Esc`/`Escape`, `Space`, `Tab`, `Backspace`, `Delete`/`Del`, `Insert`/`Ins`, `Home`, `End`, `PageUp`/`PgUp`, `PageDown`/`PgDn`, `Up`/`Down`/`Left`/`Right` (and `ArrowUp` …), `F1`–`F24` |

Modifiers must come before the key; `"K+Ctrl"` and `"Ctrl"` both throw.

**Keys are matched against `event.key`**, so write the character the key
actually produces: register `"?"`, not `"Shift+/"`. Shift is ignored for symbol
keys (browsers report `shiftKey: true` for `?`, but the symbol already carries
the meaning), and stays strict for letters, digits and named keys.

## Options

```js
createShortcuts({
  platform: "auto",        // "auto" | "mac" | "pc"
  sequenceTimeout: 1000,   // ms between the chords of a sequence
  now: Date.now,           // injectable clock, for tests
});
```

`register(spec, handler, options)`:

| Option | Default | Meaning |
| --- | --- | --- |
| `scope` | `"global"` | String or array of scope names the binding is live in. |
| `description` | `""` | Shown by the demo and useful for cheat sheets. |
| `preventDefault` | `true` | Reported on a match; the caller decides what to do. |
| `allowRepeat` | `false` | Fire while the key is held down. |
| `when` | `null` | Predicate; returning `false` suppresses the binding. |

Registering the same chord sequence twice in an overlapping scope throws an
error with `code === "DUPLICATE_SHORTCUT"`. `conflicts()` lists overlaps that
were created through different scope sets.

`register()` returns a handle with `id`, `spec`, `chordIds`, an `enabled`
getter/setter and `dispose()`.

## Sequences

Chords are buffered in a trie. If a prefix is also a complete binding (`"g"`
and `"g d"`), the registry waits, because the longer sequence might still
arrive. After `sequenceTimeout` the shorter binding fires — either on the next
event, or when you call `flush()`. Poll `flush()` from a timer if you want the
shorter binding to feel responsive.

```js
shortcuts.register("g", () => toast("Go where?"));
shortcuts.register("g d", () => go("dashboard"));

setInterval(() => shortcuts.flush(), 150);   // lets "g" fire on its own
```

## Scopes

```js
shortcuts.register("j", next, { scope: "list" });

shortcuts.handle(event);                     // global scope: not matched
shortcuts.pushScope("list");
shortcuts.handle(event);                     // matched
shortcuts.popScope("list");
```

Bindings in `"global"` are live everywhere; a binding in `"list"` is live
whenever `"list"` is on the scope stack. `handle(event, { scope: "list" })`
checks one scope explicitly without touching the stack.

## Display labels

```js
const shortcuts = createShortcuts({ platform: "pc" });
shortcuts.format("Mod+Shift+K");   // "Ctrl+Shift+K"
shortcuts.format("Ctrl+ArrowUp");  // "Ctrl+↑"
shortcuts.format("g d");           // "G D"

createShortcuts({ platform: "mac" }).format("Mod+Shift+K");   // "⇧⌘K"
```

`normalize(spec)` returns the canonical id (`"ctrl+shift+k"`), and
`matches(spec, event)` answers for a single chord without registering anything.

## API at a glance

| Method | Purpose |
| --- | --- |
| `register(spec, handler, options)` | Add a binding, returns a handle |
| `unregister(idOrSpec)` | Remove by handle id or spec, returns `boolean` |
| `handle(event, { scope })` | Feed a keydown-like object |
| `flush()` | Fire a waiting sequence once its timeout elapsed |
| `reset()` | Drop a half-typed sequence |
| `matches(spec, event)` | Single-chord predicate |
| `format(spec, { platform })` | Display label |
| `normalize(spec)` | Canonical id |
| `list()` | Registration metadata |
| `conflicts()` | Overlapping bindings |
| `pushScope(name)` / `popScope(name?)` | Scope stack |
| `activeScope`, `scopes`, `buffer`, `waiting`, `platform` | Read-only state |

`isEditableTarget(target)` is exported as well: it reports whether a DOM target
is an `input`, `textarea`, `select` or `contenteditable`, which is the usual
`when` predicate.

## Run it

```bash
cd web-frontend/keyboard-shortcuts
node --test            # 38 tests, no dependencies
```

Then open `index.html` in a browser for a live demo: a search field, a
`j`/`k` list scope, `g d` / `g t` sequences, a `?` help overlay, and a log of
what fired.
