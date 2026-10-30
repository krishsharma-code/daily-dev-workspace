/**
 * keyboard-shortcuts — a small, dependency-free shortcut registry.
 *
 * You register a shortcut spec ("Mod+K", "Shift+/", "g d"), get back a handle,
 * and feed keydown-like objects to `handle()`. The registry decides whether a
 * shortcut fired and tells you whether the caller should call
 * `preventDefault()`. It never touches the DOM, so it runs unchanged in the
 * browser and in Node (which is what the tests do).
 *
 * Features:
 *   - single chords and multi-chord sequences ("g d" = press g, then d)
 *   - "Mod" resolves to Cmd on macOS and Ctrl elsewhere
 *   - scoped shortcuts, with a scope stack so nested scopes keep their parents'
 *     bindings
 *   - optional predicates (`when`) so a shortcut can be suppressed while the
 *     user is typing
 *   - duplicate detection and a `conflicts()` report
 *   - platform-aware display labels (`format()`)
 *
 * The registry is intentionally DOM-free: it takes plain objects shaped like
 * KeyboardEvent and returns a plain result object.
 */

const UNMATCHED = Object.freeze({ matched: false, shortcut: null, pending: false });

/** Modifier tokens accepted in a spec, mapped to canonical names. */
const MODIFIERS = new Map([
  ["mod", "mod"],
  ["ctrl", "ctrl"],
  ["control", "ctrl"],
  ["alt", "alt"],
  ["opt", "alt"],
  ["option", "alt"],
  ["shift", "shift"],
  ["meta", "meta"],
  ["cmd", "meta"],
  ["command", "meta"],
  ["super", "meta"],
  ["win", "meta"],
]);

/** Non-printable key names accepted in a spec, mapped to canonical names. */
const KEY_ALIASES = new Map([
  ["enter", "enter"],
  ["return", "enter"],
  ["esc", "escape"],
  ["escape", "escape"],
  ["space", "space"],
  ["spacebar", "space"],
  ["tab", "tab"],
  ["backspace", "backspace"],
  ["del", "delete"],
  ["delete", "delete"],
  ["ins", "insert"],
  ["insert", "insert"],
  ["home", "home"],
  ["end", "end"],
  ["pageup", "pageup"],
  ["pgup", "pageup"],
  ["pagedown", "pagedown"],
  ["pgdn", "pagedown"],
  ["up", "arrowup"],
  ["arrowup", "arrowup"],
  ["down", "arrowdown"],
  ["arrowdown", "arrowdown"],
  ["left", "arrowleft"],
  ["arrowleft", "arrowleft"],
  ["right", "arrowright"],
  ["arrowright", "arrowright"],
  ["plus", "+"],
]);

/** How canonical key names are printed by `format()`. */
const KEY_LABELS = new Map([
  ["enter", "Enter"],
  ["escape", "Esc"],
  ["space", "Space"],
  ["tab", "Tab"],
  ["backspace", "Backspace"],
  ["delete", "Delete"],
  ["insert", "Ins"],
  ["home", "Home"],
  ["end", "End"],
  ["pageup", "PgUp"],
  ["pagedown", "PgDn"],
  ["arrowup", "\u2191"],
  ["arrowdown", "\u2193"],
  ["arrowleft", "\u2190"],
  ["arrowright", "\u2192"],
]);

const MAC_MODIFIER_LABELS = [
  ["ctrl", "\u2303"],
  ["alt", "\u2325"],
  ["shift", "\u21e7"],
  ["meta", "\u2318"],
];

const PC_MODIFIER_LABELS = [
  ["ctrl", "Ctrl"],
  ["alt", "Alt"],
  ["shift", "Shift"],
  ["meta", "Meta"],
];

/** Placeholder used while splitting "Ctrl++" so the literal "+" survives. */
const PLUS = "\u0001";

function describe(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return `"${value}"`;
  if (Array.isArray(value)) return "an array";
  if (typeof value === "object") return "an object";
  return String(value);
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function detectPlatform() {
  if (typeof navigator === "undefined" || navigator === null) return "pc";
  const haystack = `${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`;
  return /mac|iphone|ipad|ipod/i.test(haystack) ? "mac" : "pc";
}

function normalizePlatform(platform) {
  if (platform === undefined || platform === null || platform === "auto") {
    return detectPlatform();
  }
  if (typeof platform !== "string") {
    throw new TypeError(`platform must be a string, received ${describe(platform)}`);
  }
  const lower = platform.toLowerCase();
  if (lower === "mac" || lower === "macos" || lower === "darwin") return "mac";
  if (lower === "pc" || lower === "win" || lower === "windows" || lower === "linux") return "pc";
  throw new RangeError(`unknown platform ${describe(platform)}; use "auto", "mac" or "pc"`);
}

function normalizeKeyToken(token, specText) {
  const lower = token.toLowerCase();
  if (KEY_ALIASES.has(lower)) return KEY_ALIASES.get(lower);
  if (lower.length === 1) return lower;
  if (/^f([1-9]|1[0-9]|2[0-4])$/.test(lower)) return lower;
  throw new Error(`unknown key ${describe(token)} in shortcut ${describe(specText)}`);
}

/**
 * Parse one chord such as "Ctrl+Shift+K" into
 * `{ key, ctrl, alt, shift, meta }`, with "Mod" already resolved.
 */
function parseChord(text, platform, specText) {
  let raw = text.trim();
  if (!raw) throw new Error(`empty chord in shortcut ${describe(specText)}`);

  if (raw === "+") {
    raw = PLUS;
  } else if (raw.endsWith("++")) {
    // "Ctrl++" is Ctrl plus the literal "+" key: keep the separator, encode the key.
    raw = `${raw.slice(0, -2)}+${PLUS}`;
  }

  const tokens = raw.split("+").map((token) => (token === PLUS ? "+" : token.trim()));
  if (tokens.some((token) => token === "")) {
    throw new Error(`malformed shortcut ${describe(specText)}: empty part around "+"`);
  }

  const keyToken = tokens[tokens.length - 1];
  const modifierTokens = tokens.slice(0, -1);
  const chord = { key: null, ctrl: false, alt: false, shift: false, meta: false };

  for (const token of modifierTokens) {
    const modifier = MODIFIERS.get(token.toLowerCase());
    if (!modifier) {
      throw new Error(
        `"${token}" is not a modifier in shortcut ${describe(specText)}; modifiers come before the key`,
      );
    }
    if (modifier === "mod") {
      if (platform === "mac") chord.meta = true;
      else chord.ctrl = true;
    } else {
      chord[modifier] = true;
    }
  }

  if (MODIFIERS.has(keyToken.toLowerCase())) {
    throw new Error(`shortcut ${describe(specText)} has no key after its modifiers`);
  }
  chord.key = normalizeKeyToken(keyToken, specText);
  return chord;
}

function parseSpec(spec, platform) {
  if (typeof spec !== "string") {
    throw new TypeError(`shortcut must be a string, received ${describe(spec)}`);
  }
  const trimmed = spec.trim();
  if (!trimmed) throw new TypeError("shortcut must not be empty");
  return trimmed.split(/\s+/).map((chunk) => parseChord(chunk, platform, trimmed));
}

/**
 * Does shift add meaning to this chord?
 *
 * Shift is ignored for symbol keys ("?", "$", "+") because the symbol already
 * implies it on most layouts — the browser reports `shiftKey: true` for "?" but
 * the character is the whole point of the binding. Letters, digits and named
 * keys still match shift strictly.
 */
function shiftCounts(chord) {
  return chord.key.length > 1 || /^[a-z0-9]$/.test(chord.key);
}

/** Canonical id for a chord, e.g. `{ctrl:true, shift:true, key:"k"}` -> "ctrl+shift+k". */
function chordId(chord) {
  const parts = [];
  if (chord.ctrl) parts.push("ctrl");
  if (chord.alt) parts.push("alt");
  if (chord.shift && shiftCounts(chord)) parts.push("shift");
  if (chord.meta) parts.push("meta");
  parts.push(chord.key);
  return parts.join("+");
}

/** Turn a KeyboardEvent-like object into a canonical chord, or `null`. */
function chordFromEvent(event) {
  if (!isPlainObject(event)) return null;
  const key = event.key;
  if (typeof key !== "string" || key === "") return null;

  let normalized;
  if (key === " ") {
    normalized = "space";
  } else if (key.length === 1) {
    normalized = key.toLowerCase();
  } else {
    const lower = key.toLowerCase();
    normalized = KEY_ALIASES.get(lower) ?? lower;
  }

  return {
    key: normalized,
    ctrl: event.ctrlKey === true,
    alt: event.altKey === true,
    shift: event.shiftKey === true,
    meta: event.metaKey === true,
  };
}

function formatChord(chord, platform) {
  const label = KEY_LABELS.get(chord.key) ?? chord.key.toUpperCase();
  if (platform === "mac") {
    let out = "";
    for (const [name, glyph] of MAC_MODIFIER_LABELS) {
      if (chord[name]) out += glyph;
    }
    return out + label;
  }
  const parts = [];
  for (const [name, text] of PC_MODIFIER_LABELS) {
    if (chord[name]) parts.push(text);
  }
  parts.push(label);
  return parts.join("+");
}

/**
 * Is this DOM target a place where the user is typing?
 *
 * Accepts anything with a `tagName` and/or `isContentEditable`, so it works
 * with real elements and with plain objects in tests.
 */
export function isEditableTarget(target) {
  if (target === null || typeof target !== "object") return false;
  const tagName = typeof target.tagName === "string" ? target.tagName.toUpperCase() : "";
  if (tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT") return true;
  return target.isContentEditable === true;
}

/**
 * Create a shortcut registry.
 *
 * @param {object} [options]
 * @param {"auto"|"mac"|"pc"} [options.platform="auto"] Platform used to resolve
 *   "Mod" and to pick display labels. `"auto"` sniffs `navigator`.
 * @param {number} [options.sequenceTimeout=1000] Milliseconds allowed between
 *   the chords of a sequence before the buffer is abandoned. `0` makes
 *   sequences effectively unusable (the buffer expires on the next event).
 * @param {() => number} [options.now=Date.now] Clock, injectable for tests.
 */
export function createShortcuts(options = {}) {
  if (!isPlainObject(options)) {
    throw new TypeError(`options must be an object, received ${describe(options)}`);
  }

  const platform = normalizePlatform(options.platform);
  const sequenceTimeout = options.sequenceTimeout ?? 1000;
  if (typeof sequenceTimeout !== "number" || !Number.isFinite(sequenceTimeout) || sequenceTimeout < 0) {
    throw new RangeError(
      `sequenceTimeout must be a finite number >= 0, received ${describe(sequenceTimeout)}`,
    );
  }
  const now = options.now ?? Date.now;
  if (typeof now !== "function") {
    throw new TypeError(`now must be a function, received ${describe(now)}`);
  }

  let nextId = 0;
  /** @type {Map<number, object>} */
  const registrations = new Map();
  const scopeStack = ["global"];

  function createNode() {
    return { children: new Map(), entries: [] };
  }

  let trie = createNode();
  let buffer = [];
  let lastAt = 0;
  /** @type {{entries: object[], at: number, event: object} | null} */
  let pending = null;

  function rebuildTrie() {
    trie = createNode();
    for (const registration of registrations.values()) {
      let node = trie;
      for (const id of registration.chordIds) {
        let child = node.children.get(id);
        if (!child) {
          child = createNode();
          node.children.set(id, child);
        }
        node = child;
      }
      node.entries.push(registration);
    }
  }

  function clearBuffer() {
    buffer = [];
    pending = null;
  }

  function allowedScopes(explicit) {
    const allowed = new Set(["global"]);
    if (explicit === undefined || explicit === null) {
      for (const scope of scopeStack) allowed.add(scope);
      return allowed;
    }
    const list = Array.isArray(explicit) ? explicit : [explicit];
    for (const scope of list) {
      if (typeof scope !== "string" || !scope.trim()) {
        throw new TypeError(`scope must be a non-empty string, received ${describe(scope)}`);
      }
      allowed.add(scope);
    }
    return allowed;
  }

  function selectEntries(entries, event, allowed) {
    return entries.filter(
      (registration) =>
        registration.enabled &&
        registration.scopes.some((scope) => allowed.has(scope)) &&
        (!registration.when || registration.when(event) !== false),
    );
  }

  function invoke(registration, event) {
    if (!registration.enabled) return { ...UNMATCHED, reason: "disabled" };
    if (event && event.repeat === true && !registration.allowRepeat) {
      return { ...UNMATCHED, reason: "repeat" };
    }
    if (registration.when && registration.when(event) === false) {
      return { ...UNMATCHED, reason: "when" };
    }
    registration.handler(event, {
      spec: registration.spec,
      description: registration.description,
      chordIds: registration.chordIds.slice(),
    });
    return {
      matched: true,
      shortcut: {
        id: registration.id,
        spec: registration.spec,
        description: registration.description,
      },
      pending: false,
      preventDefault: registration.preventDefault,
    };
  }

  /** Fire a waiting sequence that has run out of time, if any. */
  function expire(timestamp) {
    if (buffer.length === 0) return null;
    if (pending && timestamp - pending.at >= sequenceTimeout) {
      const { entries, event } = pending;
      clearBuffer();
      for (const registration of entries) {
        const result = invoke(registration, event);
        if (result.matched) return result;
      }
      return { ...UNMATCHED, reason: "expired" };
    }
    if (!pending && timestamp - lastAt >= sequenceTimeout) {
      clearBuffer();
      return { ...UNMATCHED, reason: "expired" };
    }
    return null;
  }

  /**
   * Feed a keydown-like object to the registry.
   *
   * @returns {{matched: boolean, shortcut: object|null, pending: boolean,
   *   preventDefault?: boolean, reason?: string, buffer?: string[]}}
   */
  function handle(event, handleOptions = {}) {
    if (!isPlainObject(handleOptions)) {
      throw new TypeError(`handle options must be an object, received ${describe(handleOptions)}`);
    }

    const timestamp = now();
    const expired = expire(timestamp);
    if (expired && expired.matched) return expired;

    const chord = chordFromEvent(event);
    if (!chord) {
      clearBuffer();
      return { ...UNMATCHED, reason: "unusable-event" };
    }

    const allowed = allowedScopes(handleOptions.scope);
    buffer.push(chordId(chord));
    lastAt = timestamp;

    let node = trie;
    for (const part of buffer) {
      node = node.children.get(part);
      if (!node) {
        clearBuffer();
        return { ...UNMATCHED, reason: "no-match" };
      }
    }

    const candidates = selectEntries(node.entries, event, allowed);

    if (node.children.size > 0) {
      // The buffer could still grow into a longer sequence: wait.
      pending = candidates.length > 0 ? { entries: candidates, at: timestamp, event } : null;
      return { matched: false, shortcut: null, pending: true, buffer: buffer.slice() };
    }

    clearBuffer();
    if (candidates.length === 0) return { ...UNMATCHED, reason: "no-candidates" };
    return invoke(candidates[0], event);
  }

  /**
   * Commit a waiting sequence once its timeout has elapsed. Call this from a
   * timer, or on any event that means "the user stopped typing", if you want
   * a shorter sequence ("g") to fire even though "g d" also exists.
   */
  function flush() {
    if (!pending) {
      const hadBuffer = buffer.length > 0;
      clearBuffer();
      return { ...UNMATCHED, reason: hadBuffer ? "expired" : "empty" };
    }
    if (now() - pending.at < sequenceTimeout) {
      return { matched: false, shortcut: null, pending: true, buffer: buffer.slice() };
    }
    const { entries, event } = pending;
    clearBuffer();
    for (const registration of entries) {
      const result = invoke(registration, event);
      if (result.matched) return result;
    }
    return { ...UNMATCHED, reason: "expired" };
  }

  /**
   * Register a shortcut.
   *
   * @param {string} spec e.g. "Mod+K", "Shift+/", "g d"
   * @param {(event: object, info: object) => void} handler
   * @param {object} [registrationOptions]
   * @param {string|string[]} [registrationOptions.scope="global"]
   * @param {string} [registrationOptions.description=""]
   * @param {boolean} [registrationOptions.preventDefault=true] Reported back
   *   on a match so the caller can suppress the browser's default action.
   * @param {boolean} [registrationOptions.allowRepeat=false] Fire on key
   *   repeat when the key is held down.
   * @param {(event: object) => boolean} [registrationOptions.when] Extra
   *   predicate; returning `false` suppresses the shortcut.
   */
  function register(spec, handler, registrationOptions = {}) {
    if (!isPlainObject(registrationOptions)) {
      throw new TypeError(
        `registration options must be an object, received ${describe(registrationOptions)}`,
      );
    }
    if (typeof handler !== "function") {
      throw new TypeError(`handler must be a function, received ${describe(handler)}`);
    }

    const chords = parseSpec(spec, platform);
    const chordIds = chords.map(chordId);
    const signature = chordIds.join(" ");

    const scopeOption = registrationOptions.scope ?? "global";
    const scopes = Array.isArray(scopeOption) ? scopeOption.slice() : [scopeOption];
    if (scopes.length === 0) throw new TypeError("scope must not be an empty array");
    for (const scope of scopes) {
      if (typeof scope !== "string" || !scope.trim()) {
        throw new TypeError(`scope must be a non-empty string, received ${describe(scope)}`);
      }
    }
    const scopeKey = [...new Set(scopes)].sort().join("|");

    for (const other of registrations.values()) {
      if (other.signature === signature && other.scopeKey === scopeKey) {
        const error = new Error(`shortcut ${describe(spec)} is already registered in scope ${scopeKey}`);
        error.code = "DUPLICATE_SHORTCUT";
        throw error;
      }
    }

    const preventDefault = registrationOptions.preventDefault ?? true;
    if (typeof preventDefault !== "boolean") {
      throw new TypeError(`preventDefault must be a boolean, received ${describe(preventDefault)}`);
    }
    const allowRepeat = registrationOptions.allowRepeat ?? false;
    if (typeof allowRepeat !== "boolean") {
      throw new TypeError(`allowRepeat must be a boolean, received ${describe(allowRepeat)}`);
    }
    const when = registrationOptions.when ?? null;
    if (when !== null && typeof when !== "function") {
      throw new TypeError(`when must be a function, received ${describe(when)}`);
    }
    const description = registrationOptions.description ?? "";
    if (typeof description !== "string") {
      throw new TypeError(`description must be a string, received ${describe(description)}`);
    }

    const registration = {
      id: (nextId += 1),
      spec: signature,
      signature,
      scopeKey,
      scopes,
      chordIds,
      chords,
      handler,
      description,
      preventDefault,
      allowRepeat,
      when,
      enabled: true,
    };
    registrations.set(registration.id, registration);
    rebuildTrie();

    return {
      id: registration.id,
      spec: registration.spec,
      chordIds: registration.chordIds.slice(),
      get enabled() {
        return registration.enabled;
      },
      set enabled(value) {
        registration.enabled = Boolean(value);
      },
      dispose() {
        unregister(registration.id);
      },
    };
  }

  /** Remove a registration by handle id or by spec (first match wins). */
  function unregister(target) {
    if (typeof target === "number") {
      if (!Number.isInteger(target)) {
        throw new TypeError(`unregister needs an integer id, received ${describe(target)}`);
      }
      if (!registrations.delete(target)) return false;
      rebuildTrie();
      return true;
    }
    if (typeof target === "string") {
      const signature = parseSpec(target, platform).map(chordId).join(" ");
      for (const registration of registrations.values()) {
        if (registration.signature === signature) {
          registrations.delete(registration.id);
          rebuildTrie();
          return true;
        }
      }
      return false;
    }
    throw new TypeError(`unregister needs an id or a spec, received ${describe(target)}`);
  }

  /** Does this event match a single-chord spec? */
  function matches(spec, event) {
    const chords = parseSpec(spec, platform);
    if (chords.length !== 1) {
      throw new Error(`matches() needs a single-chord shortcut, received ${describe(spec)}`);
    }
    const chord = chordFromEvent(event);
    if (!chord) return false;
    return chordId(chord) === chordId(chords[0]);
  }

  /** Human-readable label for a spec, e.g. "Ctrl+Shift+K" or "⇧⌘K". */
  function format(spec, formatOptions = {}) {
    if (!isPlainObject(formatOptions)) {
      throw new TypeError(`format options must be an object, received ${describe(formatOptions)}`);
    }
    const targetPlatform =
      formatOptions.platform === undefined
        ? platform
        : normalizePlatform(formatOptions.platform);
    const chords = parseSpec(spec, targetPlatform);
    return chords.map((chord) => formatChord(chord, targetPlatform)).join(" ");
  }

  /** Canonical form of a spec with "Mod" resolved, e.g. "ctrl+k" or "g d". */
  function normalize(spec) {
    return parseSpec(spec, platform).map(chordId).join(" ");
  }

  /** Metadata for every registration, in registration order. */
  function list() {
    return [...registrations.values()].map((registration) => ({
      id: registration.id,
      spec: registration.spec,
      description: registration.description,
      scopes: registration.scopes.slice(),
      enabled: registration.enabled,
    }));
  }

  /** Pairs of registrations that share a chord sequence in an overlapping scope. */
  function conflicts() {
    const out = [];
    const entries = [...registrations.values()];
    for (let i = 0; i < entries.length; i += 1) {
      for (let j = i + 1; j < entries.length; j += 1) {
        const a = entries[i];
        const b = entries[j];
        if (a.signature !== b.signature) continue;
        const shared = a.scopes.filter((scope) => b.scopes.includes(scope));
        if (shared.length > 0) out.push({ a: a.spec, b: b.spec, scopes: shared });
      }
    }
    return out;
  }

  function pushScope(name) {
    if (typeof name !== "string" || !name.trim()) {
      throw new TypeError(`scope must be a non-empty string, received ${describe(name)}`);
    }
    scopeStack.push(name);
    return name;
  }

  function popScope(name) {
    if (scopeStack.length === 1) return scopeStack[0];
    if (name === undefined) {
      scopeStack.pop();
      return scopeStack[scopeStack.length - 1];
    }
    const index = scopeStack.lastIndexOf(name);
    if (index > 0) scopeStack.splice(index, 1);
    return scopeStack[scopeStack.length - 1];
  }

  function reset() {
    clearBuffer();
  }

  return {
    register,
    unregister,
    handle,
    flush,
    reset,
    matches,
    format,
    normalize,
    list,
    conflicts,
    pushScope,
    popScope,
    get platform() {
      return platform;
    },
    get activeScope() {
      return scopeStack[scopeStack.length - 1];
    },
    get scopes() {
      return scopeStack.slice();
    },
    /** The chord ids currently buffered, e.g. `["g"]` mid-sequence. */
    get buffer() {
      return buffer.slice();
    },
    /** Is a sequence waiting for its next chord? */
    get waiting() {
      return pending !== null || buffer.length > 0;
    },
  };
}
