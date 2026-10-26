import { test } from "node:test";
import assert from "node:assert/strict";

import { createUndoStack } from "./undo-stack.js";

/** Deterministic clock: `clock()` reads it, `clock.advance(ms)` moves it. */
function makeClock(start = 0) {
  let t = start;
  const clock = () => t;
  clock.advance = (ms) => {
    t += ms;
  };
  return clock;
}

// ---------------------------------------------------------------------------
// Basics
// ---------------------------------------------------------------------------

test("starts on the initial state with nothing to undo or redo", () => {
  const stack = createUndoStack({ initial: "a" });
  assert.equal(stack.current, "a");
  assert.equal(stack.canUndo, false);
  assert.equal(stack.canRedo, false);
  assert.equal(stack.length, 1);
  assert.equal(stack.undoDepth, 0);
  assert.equal(stack.redoDepth, 0);
});

test("push records a step and becomes current", () => {
  const stack = createUndoStack({ initial: 0 });
  const returned = stack.push(1);
  assert.equal(returned, 1);
  assert.equal(stack.current, 1);
  assert.equal(stack.canUndo, true);
  assert.equal(stack.canRedo, false);
  assert.deepEqual(stack.states(), [0, 1]);
});

test("undo walks back and redo walks forward again", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.push("b");
  stack.push("c");

  assert.equal(stack.undo(), "b");
  assert.equal(stack.undo(), "a");
  assert.equal(stack.canUndo, false);
  assert.equal(stack.canRedo, true);

  assert.equal(stack.redo(), "b");
  assert.equal(stack.redo(), "c");
  assert.equal(stack.canRedo, false);
});

test("undo at the oldest state and redo at the newest are no-ops", () => {
  const stack = createUndoStack({ initial: "only" });
  assert.equal(stack.undo(), "only");
  assert.equal(stack.redo(), "only");
  assert.equal(stack.length, 1);
});

test("undo/redo account for depth correctly", () => {
  const stack = createUndoStack({ initial: 0 });
  stack.push(1);
  stack.push(2);
  stack.push(3);
  assert.equal(stack.undoDepth, 3);
  assert.equal(stack.redoDepth, 0);

  stack.undo();
  assert.equal(stack.undoDepth, 2);
  assert.equal(stack.redoDepth, 1);
});

test("states() returns a copy that cannot corrupt the stack", () => {
  const stack = createUndoStack({ initial: 0 });
  stack.push(1);

  const view = stack.states();
  view.push(99);
  view[0] = 42;

  assert.deepEqual(stack.states(), [0, 1]);
});

test("undefined is a usable state and can be undone and redone", () => {
  const stack = createUndoStack({ initial: "start" });
  stack.push(undefined);
  assert.equal(stack.current, undefined);
  assert.equal(stack.undo(), "start");
  assert.equal(stack.redo(), undefined);
});

test("objects are stored by reference, not cloned", () => {
  const first = { text: "a" };
  const second = { text: "b" };
  const stack = createUndoStack({ initial: first });
  stack.push(second);
  assert.equal(stack.current, second);
  assert.equal(stack.undo(), first);
});

// ---------------------------------------------------------------------------
// Branching and the size limit
// ---------------------------------------------------------------------------

test("pushing after an undo drops the redo branch", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.push("b");
  stack.push("c");
  stack.undo();

  stack.push("d");
  assert.deepEqual(stack.states(), ["a", "b", "d"]);
  assert.equal(stack.current, "d");
  assert.equal(stack.canRedo, false);
});

test("the limit evicts the oldest steps", () => {
  const stack = createUndoStack({ initial: 0, limit: 2 });
  stack.push(1);
  stack.push(2);
  stack.push(3);

  assert.deepEqual(stack.states(), [1, 2, 3]);
  assert.equal(stack.undoDepth, 2);
  assert.equal(stack.undo(), 2);
  assert.equal(stack.undo(), 1);
  assert.equal(stack.canUndo, false);
});

test("a limit of 1 keeps exactly one undo step", () => {
  const stack = createUndoStack({ initial: "a", limit: 1 });
  stack.push("b");
  stack.push("c");
  assert.deepEqual(stack.states(), ["b", "c"]);
  assert.equal(stack.undo(), "b");
  assert.equal(stack.canUndo, false);
});

test("eviction keeps the redo branch intact after an undo", () => {
  const stack = createUndoStack({ initial: 0, limit: 2 });
  stack.push(1);
  stack.push(2);
  stack.undo(); // current 1, redo available
  stack.push(3); // truncates 2, then appends
  assert.deepEqual(stack.states(), [0, 1, 3]);
  assert.equal(stack.current, 3);
  assert.equal(stack.canRedo, false);
});

// ---------------------------------------------------------------------------
// Coalescing
// ---------------------------------------------------------------------------

test("coalescing merges same-key pushes inside the window", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  clock.advance(100);
  stack.push("ab", { coalesceKey: "typing" });
  clock.advance(100);
  stack.push("abc", { coalesceKey: "typing" });

  assert.deepEqual(stack.states(), ["", "abc"]);
  assert.equal(stack.undo(), "");
});

test("coalescing stops once the window has passed", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  clock.advance(600);
  stack.push("ab", { coalesceKey: "typing" });

  assert.deepEqual(stack.states(), ["", "a", "ab"]);
  assert.equal(stack.undo(), "a");
});

test("different coalesce keys never merge", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  stack.push("a!", { coalesceKey: "format" });

  assert.deepEqual(stack.states(), ["", "a", "a!"]);
});

test("a push without a key never merges", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  stack.push("b");
  stack.push("c", { coalesceKey: "typing" });

  assert.deepEqual(stack.states(), ["", "a", "b", "c"]);
});

test("coalescing is off by default", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", now: clock });

  stack.push("a", { coalesceKey: "typing" });
  stack.push("ab", { coalesceKey: "typing" });

  assert.deepEqual(stack.states(), ["", "a", "ab"]);
});

test("undo then push starts a fresh coalescing run", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  stack.push("ab", { coalesceKey: "typing" });
  stack.undo();

  stack.push("x", { coalesceKey: "typing" });
  clock.advance(50);
  stack.push("xy", { coalesceKey: "typing" });

  // The abandoned branch is gone and the new run coalesced into one step.
  assert.deepEqual(stack.states(), ["", "xy"]);
});

test("redo resets coalescing so the next push is its own step", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  stack.undo();
  stack.redo();
  stack.push("ab", { coalesceKey: "typing" });

  assert.deepEqual(stack.states(), ["", "a", "ab"]);
});

test("a clock that jumps backwards does not coalesce", () => {
  const clock = makeClock(1000);
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  clock.advance(-1000);
  stack.push("ab", { coalesceKey: "typing" });

  assert.deepEqual(stack.states(), ["", "a", "ab"]);
});

// ---------------------------------------------------------------------------
// Transactions
// ---------------------------------------------------------------------------

test("a transaction collapses several pushes into one step", () => {
  const stack = createUndoStack({ initial: "a" });

  const result = stack.transaction((history) => {
    history.push("b");
    history.push("c");
    return "done";
  });

  assert.equal(result, "done");
  assert.deepEqual(stack.states(), ["a", "c"]);
  assert.equal(stack.current, "c");
  assert.equal(stack.undo(), "a");
});

test("a transaction with no pushes records nothing", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.transaction(() => "noop");
  assert.deepEqual(stack.states(), ["a"]);
  assert.equal(stack.canUndo, false);
});

test("nested transactions flatten into a single step", () => {
  const stack = createUndoStack({ initial: "a" });

  stack.transaction((outer) => {
    outer.push("b");
    outer.transaction((inner) => {
      inner.push("c");
    });
    outer.push("d");
  });

  assert.deepEqual(stack.states(), ["a", "d"]);
});

test("a transaction that throws leaves the history untouched", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.push("b");

  assert.throws(() => {
    stack.transaction((history) => {
      history.push("c");
      history.push("d");
      throw new Error("boom");
    });
  }, /boom/);

  assert.deepEqual(stack.states(), ["a", "b"]);
  assert.equal(stack.current, "b");
  assert.equal(stack.canRedo, false);
});

test("the current state inside a transaction is the pending one", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.transaction((history) => {
    history.push("b");
    assert.equal(history.current, "b");
    assert.equal(history.inTransaction, true);
  });
  assert.equal(stack.current, "b");
  assert.equal(stack.inTransaction, false);
});

test("undo and redo refuse to run inside a transaction", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.push("b");

  stack.transaction((history) => {
    assert.throws(() => history.undo(), /inside a transaction/);
    assert.throws(() => history.redo(), /inside a transaction/);
    assert.throws(() => history.reset("z"), /inside a transaction/);
  });
});

test("a transaction is a step boundary for coalescing", () => {
  const clock = makeClock();
  const stack = createUndoStack({ initial: "", coalesceMs: 500, now: clock });

  stack.push("a", { coalesceKey: "typing" });
  stack.transaction((history) => {
    history.push("b", { coalesceKey: "typing" });
  });
  clock.advance(10);
  stack.push("c", { coalesceKey: "typing" });

  assert.deepEqual(stack.states(), ["", "a", "b", "c"]);
});

test("a transaction after an undo drops the redo branch", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.push("b");
  stack.push("c");
  stack.undo();

  stack.transaction((history) => {
    history.push("d");
  });

  assert.deepEqual(stack.states(), ["a", "b", "d"]);
  assert.equal(stack.canRedo, false);
});

// ---------------------------------------------------------------------------
// reset and subscribers
// ---------------------------------------------------------------------------

test("reset throws the history away and keeps one state", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.push("b");
  stack.push("c");

  assert.equal(stack.reset("fresh"), "fresh");
  assert.deepEqual(stack.states(), ["fresh"]);
  assert.equal(stack.canUndo, false);
  assert.equal(stack.canRedo, false);
});

test("subscribers receive a snapshot on every change", () => {
  const stack = createUndoStack({ initial: "a" });
  const seen = [];
  stack.subscribe((snapshot) => seen.push(snapshot));

  stack.push("b");
  stack.undo();

  assert.equal(seen.length, 2);
  assert.deepEqual(seen[0], {
    current: "b",
    canUndo: true,
    canRedo: false,
    undoDepth: 1,
    redoDepth: 0,
    length: 2,
  });
  assert.equal(seen[1].current, "a");
  assert.equal(seen[1].canRedo, true);
});

test("no-op undo and redo do not notify", () => {
  const stack = createUndoStack({ initial: "a" });
  let calls = 0;
  stack.subscribe(() => {
    calls += 1;
  });

  stack.undo();
  stack.redo();
  assert.equal(calls, 0);
});

test("unsubscribe stops notifications", () => {
  const stack = createUndoStack({ initial: "a" });
  let calls = 0;
  const off = stack.subscribe(() => {
    calls += 1;
  });

  stack.push("b");
  off();
  stack.push("c");

  assert.equal(calls, 1);
});

test("listeners run in registration order and may unsubscribe mid-notify", () => {
  const stack = createUndoStack({ initial: "a" });
  const order = [];

  const offFirst = stack.subscribe(() => order.push("first"));
  stack.subscribe(() => {
    order.push("second");
    offFirst();
  });

  stack.push("b");
  stack.push("c");

  // The first listener runs, then the second removes it before the next change.
  assert.deepEqual(order, ["first", "second", "second"]);
});

test("a listener that throws propagates to the caller", () => {
  const stack = createUndoStack({ initial: "a" });
  stack.subscribe(() => {
    throw new Error("listener failed");
  });
  assert.throws(() => stack.push("b"), /listener failed/);
  // The state was still recorded before the notification.
  assert.equal(stack.current, "b");
});

// ---------------------------------------------------------------------------
// Argument validation
// ---------------------------------------------------------------------------

test("invalid options are rejected", () => {
  assert.throws(() => createUndoStack(null), TypeError);
  assert.throws(() => createUndoStack([]), TypeError);
  assert.throws(() => createUndoStack({ limit: 0 }), RangeError);
  assert.throws(() => createUndoStack({ limit: -1 }), RangeError);
  assert.throws(() => createUndoStack({ limit: 1.5 }), RangeError);
  assert.throws(() => createUndoStack({ limit: "5" }), RangeError);
  assert.throws(() => createUndoStack({ coalesceMs: -1 }), RangeError);
  assert.throws(() => createUndoStack({ coalesceMs: Number.NaN }), RangeError);
  assert.throws(() => createUndoStack({ coalesceMs: Number.POSITIVE_INFINITY }), RangeError);
  assert.throws(() => createUndoStack({ now: 42 }), TypeError);
});

test("invalid push arguments are rejected", () => {
  const stack = createUndoStack({ initial: "a" });
  assert.throws(() => stack.push("b", null), TypeError);
  assert.throws(() => stack.push("b", "typing"), TypeError);
  assert.throws(() => stack.push("b", { coalesceKey: 7 }), TypeError);
  assert.equal(stack.current, "a");
});

test("invalid transaction and subscribe arguments are rejected", () => {
  const stack = createUndoStack({ initial: "a" });
  assert.throws(() => stack.transaction("nope"), TypeError);
  assert.throws(() => stack.subscribe("nope"), TypeError);
  assert.equal(stack.inTransaction, false);
});
