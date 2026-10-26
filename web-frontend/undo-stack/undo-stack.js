/**
 * undo-stack — a small, dependency-free undo/redo history manager.
 *
 * The stack is snapshot based: you push whole states (any value you like) and
 * `undo`/`redo` walk back and forth through them. Nothing is cloned, so the
 * states you get back are the exact objects you pushed.
 *
 * Runs unchanged in the browser and in Node — no DOM, no build step.
 */

const DEFAULT_LIMIT = 100;
const DEFAULT_COALESCE_MS = 0;

function describe(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return `"${value}"`;
  if (Array.isArray(value)) return "an array";
  if (typeof value === "object") return "an object";
  return String(value);
}

/**
 * Create an undo/redo stack.
 *
 * @param {object} [options]
 * @param {*} [options.initial] Starting state. Defaults to `undefined`.
 * @param {number} [options.limit=100] Maximum number of undo steps retained.
 * @param {number} [options.coalesceMs=0] Time window, in milliseconds, within
 *   which consecutive pushes carrying the same `coalesceKey` collapse into a
 *   single step. `0` disables coalescing.
 * @param {() => number} [options.now=Date.now] Clock, injectable for tests.
 * @returns {object} the stack
 */
export function createUndoStack(options = {}) {
  if (options === null || typeof options !== "object" || Array.isArray(options)) {
    throw new TypeError(`options must be an object, received ${describe(options)}`);
  }

  const {
    initial,
    limit = DEFAULT_LIMIT,
    coalesceMs = DEFAULT_COALESCE_MS,
    now = Date.now,
  } = options;

  if (!Number.isInteger(limit) || limit < 1) {
    throw new RangeError(`limit must be a positive integer, received ${describe(limit)}`);
  }
  if (typeof coalesceMs !== "number" || !Number.isFinite(coalesceMs) || coalesceMs < 0) {
    throw new RangeError(`coalesceMs must be a finite number >= 0, received ${describe(coalesceMs)}`);
  }
  if (typeof now !== "function") {
    throw new TypeError(`now must be a function, received ${describe(now)}`);
  }

  /** Committed states; `entries[index]` is the current one. */
  let entries = [initial];
  let index = 0;

  /** Coalescing bookkeeping for the most recent push. */
  let lastKey = null;
  let lastAt = 0;

  const listeners = new Set();
  /** Open transactions, outermost first. */
  const frames = [];

  function assertNoTransaction(method) {
    if (frames.length > 0) {
      throw new Error(`${method}() cannot be called inside a transaction; let it commit first`);
    }
  }

  function currentState() {
    for (let i = frames.length - 1; i >= 0; i -= 1) {
      if (frames[i].hasPending) return frames[i].state;
    }
    return entries[index];
  }

  function info() {
    return {
      current: currentState(),
      canUndo: index > 0,
      canRedo: index < entries.length - 1,
      undoDepth: index,
      redoDepth: entries.length - 1 - index,
      length: entries.length,
    };
  }

  function notify() {
    if (listeners.size === 0) return;
    // Iterate a copy so a listener may subscribe or unsubscribe mid-flight.
    const snapshot = info();
    for (const listener of [...listeners]) listener(snapshot);
  }

  /**
   * Append `state` as a new step, dropping any redo branch and evicting the
   * oldest steps beyond `limit`. Does not notify.
   */
  function appendStep(state) {
    if (index < entries.length - 1) entries = entries.slice(0, index + 1);
    entries.push(state);
    index = entries.length - 1;

    const overflow = entries.length - (limit + 1);
    if (overflow > 0) {
      entries = entries.slice(overflow);
      index -= overflow;
    }
  }

  /**
   * Record a new state.
   *
   * @param {*} state
   * @param {object} [pushOptions]
   * @param {string} [pushOptions.coalesceKey] Merges this push into the
   *   previous step when the previous push used the same key within
   *   `coalesceMs`. Ideal for keystroke-level changes.
   * @returns {*} the state that was pushed
   */
  function push(state, pushOptions = {}) {
    if (pushOptions === null || typeof pushOptions !== "object" || Array.isArray(pushOptions)) {
      throw new TypeError(`push options must be an object, received ${describe(pushOptions)}`);
    }
    const { coalesceKey } = pushOptions;
    if (coalesceKey !== undefined && typeof coalesceKey !== "string") {
      throw new TypeError(`coalesceKey must be a string, received ${describe(coalesceKey)}`);
    }

    if (frames.length > 0) {
      const frame = frames[frames.length - 1];
      frame.hasPending = true;
      frame.state = state;
      notify();
      return state;
    }

    const at = now();
    const coalescing =
      coalesceMs > 0 &&
      coalesceKey !== undefined &&
      coalesceKey === lastKey &&
      index === entries.length - 1 &&
      at >= lastAt &&
      at - lastAt <= coalesceMs;

    if (coalescing) {
      entries[entries.length - 1] = state;
    } else {
      appendStep(state);
    }

    lastKey = coalesceKey === undefined ? null : coalesceKey;
    lastAt = at;
    notify();
    return state;
  }

  /**
   * Step back one entry. A no-op at the oldest state.
   *
   * @returns {*} the current state after the call
   */
  function undo() {
    assertNoTransaction("undo");
    if (index === 0) return currentState();
    index -= 1;
    lastKey = null;
    notify();
    return currentState();
  }

  /**
   * Step forward one entry. A no-op at the newest state.
   *
   * @returns {*} the current state after the call
   */
  function redo() {
    assertNoTransaction("redo");
    if (index === entries.length - 1) return currentState();
    index += 1;
    lastKey = null;
    notify();
    return currentState();
  }

  /**
   * Throw the history away and start again from `state`.
   *
   * @param {*} state
   * @returns {*} the state
   */
  function reset(state) {
    assertNoTransaction("reset");
    entries = [state];
    index = 0;
    lastKey = null;
    notify();
    return state;
  }

  /**
   * Run `fn` so that every push inside it collapses into a single step.
   * If `fn` throws, the pending state is discarded and the error rethrown, so
   * the history is left exactly as it was.
   *
   * Transactions may nest; only the outermost one records a step.
   *
   * @param {(stack: object) => *} fn
   * @returns {*} whatever `fn` returned
   */
  function transaction(fn) {
    if (typeof fn !== "function") {
      throw new TypeError(`transaction expects a function, received ${describe(fn)}`);
    }

    const frame = { hasPending: false, state: undefined };
    frames.push(frame);

    let result;
    try {
      result = fn(api);
    } catch (error) {
      frames.pop();
      if (frame.hasPending) notify();
      throw error;
    }

    frames.pop();

    if (frames.length > 0) {
      const parent = frames[frames.length - 1];
      if (frame.hasPending) {
        parent.hasPending = true;
        parent.state = frame.state;
      }
      return result;
    }

    if (frame.hasPending) {
      appendStep(frame.state);
      // A transaction is a step boundary: the next push starts a fresh one.
      lastKey = null;
      lastAt = 0;
      notify();
    }

    return result;
  }

  /**
   * Listen for every change (push, undo, redo, reset, transaction).
   * The listener receives `{ current, canUndo, canRedo, undoDepth, redoDepth,
   * length }`. Listeners run in registration order and a listener that throws
   * propagates to whoever triggered the change, so keep them infallible.
   *
   * @param {(info: object) => void} listener
   * @returns {() => void} unsubscribe
   */
  function subscribe(listener) {
    if (typeof listener !== "function") {
      throw new TypeError(`listener must be a function, received ${describe(listener)}`);
    }
    listeners.add(listener);
    return function unsubscribe() {
      listeners.delete(listener);
    };
  }

  const api = {
    push,
    undo,
    redo,
    reset,
    transaction,
    subscribe,
    /** Copy of the committed states, oldest first. */
    states() {
      return entries.slice();
    },
    get current() {
      return currentState();
    },
    get canUndo() {
      return index > 0;
    },
    get canRedo() {
      return index < entries.length - 1;
    },
    get undoDepth() {
      return index;
    },
    get redoDepth() {
      return entries.length - 1 - index;
    },
    get length() {
      return entries.length;
    },
    get limit() {
      return limit;
    },
    get inTransaction() {
      return frames.length > 0;
    },
  };

  return api;
}
