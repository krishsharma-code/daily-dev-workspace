# undo-stack

A small, dependency-free undo/redo history manager that runs unchanged in the
browser and in Node. You push whole snapshots of your state; `undo` and `redo`
walk back and forth through them.

It is built for the fiddly parts of a real editor rather than for a toy demo:
**keystroke coalescing** (a burst of typing becomes one undo step instead of
forty), **transactions** (a multi-part edit that undoes as one), a bounded
**history limit**, and a **subscribe** hook so the UI redraws itself.

## What it does

| Member | Purpose |
| --- | --- |
| `createUndoStack({ initial, limit, coalesceMs, now })` | Create a stack. `limit` defaults to `100` steps, `coalesceMs` to `0` (coalescing off). |
| `push(state, { coalesceKey })` | Record a new state and make it current; returns the state. |
| `undo()` / `redo()` | Step back / forward. No-ops at either end. Both return the current state. |
| `reset(state)` | Throw the history away and start again from `state`. |
| `transaction(fn)` | Run `fn(stack)` so every push inside it collapses into one step. Returns whatever `fn` returned. |
| `subscribe(listener)` | Call `listener({ current, canUndo, canRedo, undoDepth, redoDepth, length })` on every change; returns an unsubscribe function. |
| `current` | The state you are sitting on. |
| `canUndo` / `canRedo` | Whether `undo()` / `redo()` would move. |
| `undoDepth` / `redoDepth` | Steps behind / ahead of the current state. |
| `length` | Number of snapshots held, including the initial one. |
| `states()` | Copy of the snapshots, oldest first. |
| `inTransaction` | `true` while a transaction is open. |

States are stored **by reference**, never cloned: if you mutate the object you
pushed, the history changes with it. Push a fresh object per edit
(`{ ...state, text: next }`) and the history stays honest.

## Layout

```
web-frontend/undo-stack/
├── README.md
├── undo-stack.js          # implementation (ES module)
├── undo-stack.css         # styles for the demo
├── index.html             # runnable note-editor demo
├── package.json           # `npm test` runs the suite
└── undo-stack.test.mjs    # node:test suite
```

## Try the demo

The demo is an ES module, so serve the folder over HTTP rather than opening the
file directly (module scripts are blocked on `file://`):

```bash
cd web-frontend/undo-stack
python3 -m http.server 8000
# then open http://localhost:8000/
```

Type in the note (one undo step per burst of typing), add and remove tags (one
step each), press **Format** to watch a transaction undo two edits at once, and
use <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd>
(or <kbd>Ctrl</kbd>+<kbd>Y</kbd>) for undo and redo. The right-hand panel is the
stack itself: the highlighted row is the current state, faded rows below it are
the redo branch.

## Run the tests

```bash
cd web-frontend/undo-stack
node --test
```

37 tests, no third-party dependencies; Node 18+ is enough.

## Example

```js
import { createUndoStack } from "./undo-stack.js";

const history = createUndoStack({ initial: { text: "" }, limit: 50, coalesceMs: 500 });

// A burst of typing collapses into one undo step.
history.push({ text: "h" }, { coalesceKey: "typing" });
history.push({ text: "he" }, { coalesceKey: "typing" });
history.push({ text: "hello" }, { coalesceKey: "typing" });
history.undo();               // { text: "" } — the whole burst is gone

// Two edits, one step.
history.transaction((h) => {
  h.push({ text: "hello" });
  h.push({ text: "hello world" });
});
history.undo();               // { text: "" } — both edits undone together
history.redo();               // { text: "hello world" }

history.subscribe(({ canUndo, canRedo }) => {
  document.querySelector("#undo").disabled = !canUndo;
  document.querySelector("#redo").disabled = !canRedo;
});
```

## Behaviour notes

- **Pushing after an undo drops the redo branch.** That is the usual editor
  behaviour: once you type over an undone state, the future you came from is
  gone.
- **Coalescing needs a key and a window.** Two pushes merge only when both carry
  the same `coalesceKey`, no undo/redo/reset/transaction happened in between, and
  the second arrives within `coalesceMs` of the first. Time is read through the
  injectable `now` option, so tests never sleep.
- **The limit bounds undo steps, not snapshots.** `limit: 2` holds at most three
  snapshots — the initial state plus two you can undo back through — and evicts
  the oldest first.
- **A transaction is atomic.** If `fn` throws, the pending state is discarded,
  nothing is recorded, and the error is rethrown, so a half-applied edit never
  lands in the history. Transactions nest; only the outermost one records a step.
- **Transactions and undo/redo don't mix.** `undo()`, `redo()` and `reset()`
  throw while a transaction is open — end the transaction first.
- **`current` inside a transaction is the pending state**, so `fn` can read what
  it has built so far. `states()` still returns committed snapshots only.
- **Subscribers run in registration order** and may unsubscribe mid-notification;
  a listener that throws propagates to whoever triggered the change, so keep
  listeners infallible.
