import { test } from "node:test";
import assert from "node:assert/strict";

import { createShortcuts, isEditableTarget } from "./keyboard-shortcuts.js";

/** Deterministic clock: `clock()` reads it, `clock.advance(ms)` moves it. */
function makeClock(start = 1000) {
  let t = start;
  const clock = () => t;
  clock.advance = (ms) => {
    t += ms;
  };
  return clock;
}

/** Build a keydown-like object. */
function key(k, extra = {}) {
  return { key: k, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...extra };
}

function make(platform = "pc", extra = {}) {
  const clock = makeClock();
  const shortcuts = createShortcuts({ platform, now: clock, ...extra });
  return { shortcuts, clock };
}

// ---------------------------------------------------------------------------
// Registration and basic matching
// ---------------------------------------------------------------------------

test("a registered chord fires and reports its metadata", () => {
  const { shortcuts } = make();
  const seen = [];
  shortcuts.register("Mod+K", (event, info) => seen.push([event.key, info]));

  const result = shortcuts.handle(key("k", { ctrlKey: true }));

  assert.equal(result.matched, true);
  assert.equal(result.preventDefault, true);
  assert.equal(result.shortcut.spec, "ctrl+k");
  assert.deepEqual(seen, [["k", { spec: "ctrl+k", description: "", chordIds: ["ctrl+k"] }]]);
});

test("an unregistered chord does not fire", () => {
  const { shortcuts } = make();
  shortcuts.register("Mod+K", () => {});
  assert.equal(shortcuts.handle(key("k")).matched, false);
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true, shiftKey: true })).matched, false);
});

test("extra modifiers do not match a chord that did not ask for them", () => {
  const { shortcuts } = make();
  shortcuts.register("Ctrl+K", () => {});
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).matched, true);
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true, altKey: true })).matched, false);
});

test("shift is matched explicitly and case-insensitively", () => {
  const { shortcuts } = make();
  let fired = 0;
  shortcuts.register("Shift+K", () => {
    fired += 1;
  });

  assert.equal(shortcuts.handle(key("K", { shiftKey: true })).matched, true);
  assert.equal(shortcuts.handle(key("k", { shiftKey: true })).matched, true);
  assert.equal(shortcuts.handle(key("k")).matched, false);
  assert.equal(fired, 2);
});

test("symbol keys match the character the key produces, ignoring the implied shift", () => {
  const { shortcuts } = make();
  shortcuts.register("?", () => {});

  // Browsers report "?" with shiftKey true; the shift flag is implied, not required.
  assert.equal(shortcuts.handle(key("?", { shiftKey: true })).matched, true);
  assert.equal(shortcuts.handle(key("?")).matched, true);
  assert.equal(shortcuts.handle(key("/")).matched, false);
});

test("shift stays strict for letters, digits and named keys", () => {
  const { shortcuts } = make();
  shortcuts.register("Shift+K", () => {});
  shortcuts.register("Shift+1", () => {});

  assert.equal(shortcuts.handle(key("k")).matched, false);
  assert.equal(shortcuts.handle(key("!", { shiftKey: true })).matched, false);
  assert.equal(shortcuts.handle(key("1", { shiftKey: true })).matched, true);
});

test("a literal plus key can be registered", () => {
  const { shortcuts } = make();
  shortcuts.register("Ctrl++", () => {});
  assert.equal(shortcuts.handle(key("+", { ctrlKey: true })).matched, true);
  assert.equal(shortcuts.handle(key("+")).matched, false);
});

test("named keys match their KeyboardEvent spellings", () => {
  const { shortcuts } = make();
  shortcuts.register("Escape", () => {});
  shortcuts.register("Ctrl+Enter", () => {});
  shortcuts.register("Shift+ArrowUp", () => {});

  assert.equal(shortcuts.handle(key("Escape")).matched, true);
  assert.equal(shortcuts.handle(key("Enter", { ctrlKey: true })).matched, true);
  assert.equal(shortcuts.handle(key("ArrowUp", { shiftKey: true })).matched, true);
  assert.equal(shortcuts.handle(key("Enter")).matched, false);
});

test("space is normalised from the single-space key value", () => {
  const { shortcuts } = make();
  shortcuts.register("Space", () => {});
  assert.equal(shortcuts.handle(key(" ")).matched, true);
});

// ---------------------------------------------------------------------------
// Mod resolution
// ---------------------------------------------------------------------------

test("Mod resolves to ctrl on pc and meta on mac", () => {
  const pc = make("pc").shortcuts;
  const mac = make("mac").shortcuts;

  assert.equal(pc.normalize("Mod+K"), "ctrl+k");
  assert.equal(mac.normalize("Mod+K"), "meta+k");

  pc.register("Mod+K", () => {});
  mac.register("Mod+K", () => {});

  assert.equal(pc.handle(key("k", { ctrlKey: true })).matched, true);
  assert.equal(pc.handle(key("k", { metaKey: true })).matched, false);
  assert.equal(mac.handle(key("k", { metaKey: true })).matched, true);
  assert.equal(mac.handle(key("k", { ctrlKey: true })).matched, false);
});

test("modifier aliases are accepted", () => {
  const { shortcuts } = make();
  assert.equal(shortcuts.normalize("Control+Alt+Shift+Meta+K"), "ctrl+alt+shift+meta+k");
  assert.equal(shortcuts.normalize("Cmd+Option+K"), "alt+meta+k");
  assert.equal(shortcuts.normalize("Ctrl+Up"), "ctrl+arrowup");
});

// ---------------------------------------------------------------------------
// Sequences
// ---------------------------------------------------------------------------

test("a two-chord sequence fires on the second chord", () => {
  const { shortcuts } = make();
  let fired = 0;
  shortcuts.register("g d", () => {
    fired += 1;
  });

  const first = shortcuts.handle(key("g"));
  assert.equal(first.matched, false);
  assert.equal(first.pending, true);
  assert.deepEqual(shortcuts.buffer, ["g"]);
  assert.equal(shortcuts.waiting, true);

  const second = shortcuts.handle(key("d"));
  assert.equal(second.matched, true);
  assert.equal(fired, 1);
  assert.deepEqual(shortcuts.buffer, []);
  assert.equal(shortcuts.waiting, false);
});

test("an off-path chord resets the sequence buffer", () => {
  const { shortcuts } = make();
  shortcuts.register("g d", () => {});
  shortcuts.handle(key("g"));

  const stray = shortcuts.handle(key("x"));
  assert.equal(stray.matched, false);
  assert.equal(stray.reason, "no-match");
  assert.deepEqual(shortcuts.buffer, []);

  assert.equal(shortcuts.handle(key("d")).matched, false);
});

test("a sequence is abandoned once the timeout elapses", () => {
  const { shortcuts, clock } = make("pc", { sequenceTimeout: 500 });
  let fired = 0;
  shortcuts.register("g d", () => {
    fired += 1;
  });

  shortcuts.handle(key("g"));
  clock.advance(600);
  const late = shortcuts.handle(key("d"));

  assert.equal(late.matched, false);
  assert.equal(fired, 0);
  assert.deepEqual(shortcuts.buffer, []);
});

test("a sequence still completes inside the timeout", () => {
  const { shortcuts, clock } = make("pc", { sequenceTimeout: 500 });
  let fired = 0;
  shortcuts.register("g d", () => {
    fired += 1;
  });

  shortcuts.handle(key("g"));
  clock.advance(200);
  assert.equal(shortcuts.handle(key("d")).matched, true);
  assert.equal(fired, 1);
});

test("a shorter prefix waits when a longer sequence also exists, then flushes", () => {
  const { shortcuts, clock } = make("pc", { sequenceTimeout: 500 });
  const fired = [];
  shortcuts.register("g", () => fired.push("g"));
  shortcuts.register("g d", () => fired.push("g d"));

  const first = shortcuts.handle(key("g"));
  assert.equal(first.pending, true);
  assert.deepEqual(fired, []);

  // Not yet expired: nothing fires.
  assert.equal(shortcuts.flush().pending, true);
  assert.deepEqual(fired, []);

  clock.advance(500);
  const flushed = shortcuts.flush();
  assert.equal(flushed.matched, true);
  assert.equal(flushed.shortcut.spec, "g");
  assert.deepEqual(fired, ["g"]);
});

test("the waiting prefix fires on the next event after the timeout", () => {
  const { shortcuts, clock } = make("pc", { sequenceTimeout: 500 });
  const fired = [];
  shortcuts.register("g", () => fired.push("g"));
  shortcuts.register("g d", () => fired.push("g d"));

  shortcuts.handle(key("g"));
  clock.advance(500);
  const result = shortcuts.handle(key("x"));

  assert.equal(result.matched, true);
  assert.equal(result.shortcut.spec, "g");
  assert.deepEqual(fired, ["g"]);
});

test("reset() drops a half-typed sequence", () => {
  const { shortcuts } = make();
  shortcuts.register("g d", () => {});
  shortcuts.handle(key("g"));
  assert.equal(shortcuts.waiting, true);
  shortcuts.reset();
  assert.equal(shortcuts.waiting, false);
  assert.equal(shortcuts.handle(key("d")).matched, false);
});

test("a three-chord sequence needs all three", () => {
  const { shortcuts } = make();
  let fired = 0;
  shortcuts.register("g i t", () => {
    fired += 1;
  });

  shortcuts.handle(key("g"));
  shortcuts.handle(key("i"));
  assert.equal(fired, 0);
  assert.equal(shortcuts.handle(key("t")).matched, true);
  assert.equal(fired, 1);
});

// ---------------------------------------------------------------------------
// Scopes
// ---------------------------------------------------------------------------

test("a scoped shortcut only fires inside its scope", () => {
  const { shortcuts } = make();
  let fired = 0;
  shortcuts.register("j", () => {
    fired += 1;
  }, { scope: "list" });

  assert.equal(shortcuts.handle(key("j")).matched, false);
  assert.equal(shortcuts.handle(key("j"), { scope: "list" }).matched, true);
  assert.equal(fired, 1);
});

test("the scope stack gates shortcuts and keeps global ones live", () => {
  const { shortcuts } = make();
  const fired = [];
  shortcuts.register("Mod+K", () => fired.push("global"));
  shortcuts.register("j", () => fired.push("list"), { scope: "list" });
  shortcuts.register("j", () => fired.push("editor"), { scope: "editor" });

  assert.equal(shortcuts.activeScope, "global");
  shortcuts.pushScope("list");
  assert.equal(shortcuts.activeScope, "list");

  assert.equal(shortcuts.handle(key("j")).matched, true);
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).matched, true);
  assert.deepEqual(fired, ["list", "global"]);

  shortcuts.pushScope("editor");
  assert.deepEqual(shortcuts.scopes, ["global", "list", "editor"]);
  shortcuts.handle(key("j"));
  assert.deepEqual(fired, ["list", "global", "list"]);

  shortcuts.popScope();
  assert.equal(shortcuts.activeScope, "list");
  shortcuts.popScope();
  assert.equal(shortcuts.activeScope, "global");
  assert.equal(shortcuts.handle(key("j")).matched, false);
});

test("popScope(name) removes a named scope and never pops the base", () => {
  const { shortcuts } = make();
  shortcuts.pushScope("a");
  shortcuts.pushScope("b");
  assert.equal(shortcuts.popScope("a"), "b");
  assert.equal(shortcuts.popScope(), "global");
  assert.equal(shortcuts.popScope(), "global");
  assert.deepEqual(shortcuts.scopes, ["global"]);
});

// ---------------------------------------------------------------------------
// Options: when, repeat, preventDefault, enabled
// ---------------------------------------------------------------------------

test("when can suppress a shortcut, e.g. while typing", () => {
  const { shortcuts } = make();
  let fired = 0;
  shortcuts.register("j", () => {
    fired += 1;
  }, { when: (event) => !isEditableTarget(event.target) });

  assert.equal(shortcuts.handle({ ...key("j"), target: { tagName: "INPUT" } }).matched, false);
  assert.equal(shortcuts.handle({ ...key("j"), target: { tagName: "DIV" } }).matched, true);
  assert.equal(fired, 1);
});

test("key repeat is ignored unless allowRepeat is set", () => {
  const { shortcuts } = make();
  let fired = 0;
  shortcuts.register("j", () => {
    fired += 1;
  });
  shortcuts.register("k", () => {
    fired += 1;
  }, { allowRepeat: true });

  const repeated = shortcuts.handle({ ...key("j"), repeat: true });
  assert.equal(repeated.matched, false);
  assert.equal(repeated.reason, "repeat");

  assert.equal(shortcuts.handle({ ...key("k"), repeat: true }).matched, true);
  assert.equal(fired, 1);
});

test("preventDefault is reported, not applied", () => {
  const { shortcuts } = make();
  shortcuts.register("Mod+S", () => {}, { preventDefault: false });
  shortcuts.register("Mod+K", () => {});

  assert.equal(shortcuts.handle(key("s", { ctrlKey: true })).preventDefault, false);
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).preventDefault, true);
});

test("a disabled handle stops firing and can be re-enabled", () => {
  const { shortcuts } = make();
  let fired = 0;
  const handle = shortcuts.register("Mod+K", () => {
    fired += 1;
  });

  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).matched, true);
  handle.enabled = false;
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).matched, false);
  handle.enabled = true;
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).matched, true);
  assert.equal(fired, 2);
});

test("dispose removes the registration", () => {
  const { shortcuts } = make();
  const handle = shortcuts.register("Mod+K", () => {});
  handle.dispose();
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).matched, false);
  assert.deepEqual(shortcuts.list(), []);
});

test("unregister accepts an id or a spec", () => {
  const { shortcuts } = make();
  const handle = shortcuts.register("Mod+K", () => {});
  shortcuts.register("Mod+J", () => {});

  assert.equal(shortcuts.unregister(handle.id), true);
  assert.equal(shortcuts.unregister(handle.id), false);
  assert.equal(shortcuts.unregister("Mod+J"), true);
  assert.equal(shortcuts.unregister("Mod+J"), false);
  assert.equal(shortcuts.unregister("Mod+P"), false);
  assert.equal(shortcuts.list().length, 0);
});

// ---------------------------------------------------------------------------
// Duplicates and conflicts
// ---------------------------------------------------------------------------

test("registering the same spec twice in the same scope is rejected", () => {
  const { shortcuts } = make();
  shortcuts.register("Mod+K", () => {});

  assert.throws(() => shortcuts.register("Ctrl+K", () => {}), (error) => {
    assert.equal(error.code, "DUPLICATE_SHORTCUT");
    assert.match(error.message, /already registered/);
    return true;
  });
});

test("the same spec may live in different scopes, and conflicts() reports overlap", () => {
  const { shortcuts } = make();
  shortcuts.register("j", () => {}, { scope: "list" });
  shortcuts.register("j", () => {}, { scope: "editor" });
  assert.deepEqual(shortcuts.conflicts(), []);

  shortcuts.register("j", () => {}, { scope: ["global", "list"] });
  const conflicts = shortcuts.conflicts();
  assert.equal(conflicts.length, 1);
  assert.deepEqual(conflicts[0].scopes, ["list"]);
});

test("list() describes registrations", () => {
  const { shortcuts } = make();
  shortcuts.register("Mod+K", () => {}, { description: "Open search" });
  shortcuts.register("g d", () => {}, { scope: "list" });

  assert.deepEqual(shortcuts.list(), [
    { id: 1, spec: "ctrl+k", description: "Open search", scopes: ["global"], enabled: true },
    { id: 2, spec: "g d", description: "", scopes: ["list"], enabled: true },
  ]);
});

// ---------------------------------------------------------------------------
// Formatting and matching helpers
// ---------------------------------------------------------------------------

test("format prints platform-appropriate labels", () => {
  const pc = make("pc").shortcuts;
  assert.equal(pc.format("Mod+Shift+K"), "Ctrl+Shift+K");
  assert.equal(pc.format("Ctrl+ArrowUp"), "Ctrl+\u2191");
  assert.equal(pc.format("Escape"), "Esc");
  assert.equal(pc.format("g d"), "G D");

  const mac = make("mac").shortcuts;
  assert.equal(mac.format("Mod+Shift+K"), "\u21e7\u2318K");
  assert.equal(mac.format("Alt+Backspace"), "\u2325Backspace");
  assert.equal(mac.format("Mod+Shift+K", { platform: "pc" }), "Ctrl+Shift+K");
});

test("matches() answers for a single chord without registering anything", () => {
  const { shortcuts } = make();
  assert.equal(shortcuts.matches("Mod+K", key("k", { ctrlKey: true })), true);
  assert.equal(shortcuts.matches("Mod+K", key("k")), false);
  assert.equal(shortcuts.matches("Shift+Enter", key("Enter", { shiftKey: true })), true);
  assert.throws(() => shortcuts.matches("g d", key("g")), /single-chord/);
});

test("unusable events are ignored", () => {
  const { shortcuts } = make();
  shortcuts.register("Mod+K", () => {});
  for (const bad of [null, undefined, 42, "k", {}, { key: "" }]) {
    const result = shortcuts.handle(bad);
    assert.equal(result.matched, false);
    assert.equal(result.reason, "unusable-event");
  }
});

// ---------------------------------------------------------------------------
// isEditableTarget
// ---------------------------------------------------------------------------

test("isEditableTarget recognises typing surfaces", () => {
  assert.equal(isEditableTarget({ tagName: "INPUT" }), true);
  assert.equal(isEditableTarget({ tagName: "textarea" }), true);
  assert.equal(isEditableTarget({ tagName: "SELECT" }), true);
  assert.equal(isEditableTarget({ tagName: "DIV", isContentEditable: true }), true);
  assert.equal(isEditableTarget({ tagName: "DIV" }), false);
  assert.equal(isEditableTarget(null), false);
  assert.equal(isEditableTarget(undefined), false);
  assert.equal(isEditableTarget("input"), false);
});

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

test("factory options are validated", () => {
  assert.throws(() => createShortcuts(null), TypeError);
  assert.throws(() => createShortcuts({ platform: "amiga" }), RangeError);
  assert.throws(() => createShortcuts({ sequenceTimeout: -1 }), RangeError);
  assert.throws(() => createShortcuts({ sequenceTimeout: Number.POSITIVE_INFINITY }), RangeError);
  assert.throws(() => createShortcuts({ now: 5 }), TypeError);
  assert.equal(createShortcuts({ sequenceTimeout: 0 }).platform, "pc");
});

test("registration input is validated", () => {
  const { shortcuts } = make();
  assert.throws(() => shortcuts.register("", () => {}), TypeError);
  assert.throws(() => shortcuts.register("   ", () => {}), TypeError);
  assert.throws(() => shortcuts.register(42, () => {}), TypeError);
  assert.throws(() => shortcuts.register("Mod+K", "nope"), TypeError);
  assert.throws(() => shortcuts.register("Mod+K", () => {}, { scope: [] }), TypeError);
  assert.throws(() => shortcuts.register("Mod+K", () => {}, { scope: "" }), TypeError);
  assert.throws(() => shortcuts.register("Mod+K", () => {}, { when: true }), TypeError);
  assert.throws(() => shortcuts.register("Mod+K", () => {}, { preventDefault: "yes" }), TypeError);
  assert.throws(() => shortcuts.register("Hyper+K", () => {}), /not a modifier/);
  assert.throws(() => shortcuts.register("Ctrl+Hyper", () => {}), /unknown key/);
  assert.throws(() => shortcuts.register("K+Ctrl", () => {}), /not a modifier/);
  assert.throws(() => shortcuts.register("Ctrl", () => {}), /no key/);
  assert.throws(() => shortcuts.register("Ctrl+", () => {}), /malformed|empty/);
  assert.throws(() => shortcuts.unregister(1.5), TypeError);
  assert.throws(() => shortcuts.handle(key("k"), "list"), TypeError);
  assert.throws(() => shortcuts.format("Mod+K", "mac"), TypeError);
});

test("invalid handles leave the registry untouched", () => {
  const { shortcuts } = make();
  assert.throws(() => shortcuts.register("Hyper+K", () => {}));
  assert.throws(() => shortcuts.register("Ctrl+Hyper", () => {}));
  assert.deepEqual(shortcuts.list(), []);
  assert.equal(shortcuts.handle(key("k", { ctrlKey: true })).matched, false);
});
