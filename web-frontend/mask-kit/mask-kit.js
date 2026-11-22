/**
 * mask-kit — input masks with a caret that behaves.
 *
 * Dependency-free ES module that runs unchanged in the browser and in Node.
 *
 * A mask is a small pattern language:
 *
 *   `0`  a digit
 *   `A`  a letter (A-Z, a-z)
 *   `*`  a letter or a digit
 *   any other character is a literal, inserted for you
 *   `\0`, `\A`, `\*` and `\\` are literal tokens
 *
 * Everything else is derived from the pattern: which characters are kept, where
 * the separators go, how much of the mask is filled, and — the part most mask
 * libraries get wrong — where the caret lands after an edit.
 *
 *   applyMask("9876543210", "00000 00000");   // "98765 43210"
 *   applyMask("12", "00/00");                 // "12"      (no trailing "/")
 *   applyMask("12ab34", "00/00");             // "12/34"   (letters dropped)
 *   unmask("12/34", "00/00");                 // "1234"
 *   isComplete("12/34", "00/00");             // true
 *
 * Presets cover the Indian identifiers that show up in real forms — Aadhaar,
 * PAN, GSTIN, IFSC, vehicle registration, mobile, PIN code — plus card, OTP and
 * date masks.
 */

/* ------------------------------------------------------------------ */
/* Presets                                                             */
/* ------------------------------------------------------------------ */

/**
 * Ready-made masks. `label` and `hint` are for building UI; `pattern` is what
 * the rest of this module consumes, and `uppercase` is the usual expectation
 * for identifier fields.
 */
export const PRESETS = Object.freeze({
  aadhaar: {
    pattern: "0000 0000 0000",
    label: "Aadhaar",
    hint: "12 digits, grouped 4-4-4",
    uppercase: false,
  },
  pan: {
    pattern: "AAAAA0000A",
    label: "PAN",
    hint: "5 letters, 4 digits, 1 letter",
    uppercase: true,
  },
  gstin: {
    pattern: "00AAAAA0000A***",
    label: "GSTIN",
    hint: "state code, 10-char PAN, entity code, the fixed Z, checksum",
    uppercase: true,
  },
  ifsc: {
    pattern: "AAAA0000000",
    label: "IFSC",
    hint: "4 letters, then 7 alphanumerics",
    uppercase: true,
  },
  mobile: {
    pattern: "00000 00000",
    label: "Mobile",
    hint: "10 digits, grouped 5-5 (+91 shown outside the field)",
    uppercase: false,
  },
  pincode: {
    pattern: "000000",
    label: "PIN code",
    hint: "6 digits",
    uppercase: false,
  },
  vehicle: {
    pattern: "AA 00 AA 0000",
    label: "Vehicle",
    hint: "state, RTO, series, number",
    uppercase: true,
  },
  card: {
    pattern: "0000 0000 0000 0000",
    label: "Card",
    hint: "16 digits, grouped 4-4-4-4",
    uppercase: false,
  },
  expiry: {
    pattern: "00/00",
    label: "Card expiry",
    hint: "MM/YY",
    uppercase: false,
  },
  otp: {
    pattern: "000000",
    label: "OTP",
    hint: "6 digits",
    uppercase: false,
  },
  date: {
    pattern: "00/00/0000",
    label: "Date",
    hint: "DD/MM/YYYY",
    uppercase: false,
  },
});

/* ------------------------------------------------------------------ */
/* Pattern parsing                                                     */
/* ------------------------------------------------------------------ */

const PATTERN_CACHE = new Map();

function isDigit(ch) {
  return ch >= "0" && ch <= "9";
}

function isLetter(ch) {
  const code = ch.charCodeAt(0);
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122);
}

function isAlnum(ch) {
  return isDigit(ch) || isLetter(ch);
}

const KIND_TEST = { digit: isDigit, letter: isLetter, alnum: isAlnum };
const TOKEN_KIND = { 0: "digit", A: "letter", "*": "alnum" };

/**
 * Turn a mask pattern into a token list.
 *
 * @param {string} pattern
 * @returns {Array<{kind: "literal", char: string} | {kind: "digit"|"letter"|"alnum"}>}
 */
export function parsePattern(pattern) {
  if (typeof pattern !== "string" || pattern.length === 0) {
    throw new TypeError("mask-kit: pattern must be a non-empty string");
  }

  const tokens = [];
  for (let i = 0; i < pattern.length; i += 1) {
    const ch = pattern[i];

    if (ch === "\\") {
      const next = pattern[i + 1];
      if (next === undefined) {
        throw new Error("mask-kit: pattern ends with a dangling escape");
      }
      tokens.push({ kind: "literal", char: next });
      i += 1;
      continue;
    }

    const kind = TOKEN_KIND[ch];
    tokens.push(kind ? { kind } : { kind: "literal", char: ch });
  }

  return tokens;
}

function tokensFor(pattern) {
  let tokens = PATTERN_CACHE.get(pattern);
  if (!tokens) {
    tokens = parsePattern(pattern);
    PATTERN_CACHE.set(pattern, tokens);
  }
  return tokens;
}

/** Number of fillable positions in a pattern. */
export function slotCount(pattern) {
  let count = 0;
  for (const token of tokensFor(pattern)) {
    if (token.kind !== "literal") count += 1;
  }
  return count;
}

/* ------------------------------------------------------------------ */
/* Masking                                                             */
/* ------------------------------------------------------------------ */

function resolveOptions(options) {
  return {
    placeholder: options.placeholder ?? null,
    uppercase: Boolean(options.uppercase),
  };
}

/**
 * Pull the characters that belong in slots, in order.
 *
 * Separators in the input are skipped rather than consumed, so `12/34`,
 * `12-34` and `1234` all fill the same mask. A character that cannot fill the
 * next slot is dropped, which is what makes typing a letter into a digit mask a
 * no-op instead of a rejected keystroke.
 */
function collect(value, tokens, opts) {
  const chars = [];
  let tokenIndex = 0;

  for (let i = 0; i < value.length; i += 1) {
    let ch = value[i];
    if (opts.uppercase) ch = ch.toUpperCase();

    // Separators belong to the pattern, not the input, so skip them — unless
    // the input carries this exact separator, in which case it is consumed
    // here and never reaches a slot. That keeps a pasted `27…F1Z5` intact
    // instead of feeding its `Z` to the checksum slot.
    let consumedByLiteral = false;
    while (tokenIndex < tokens.length && tokens[tokenIndex].kind === "literal") {
      const literal = tokens[tokenIndex];
      tokenIndex += 1;
      if (literal.char === ch) {
        consumedByLiteral = true;
        break;
      }
    }
    if (consumedByLiteral) continue;
    if (tokenIndex >= tokens.length) break;

    if (KIND_TEST[tokens[tokenIndex].kind](ch)) {
      chars.push(ch);
      tokenIndex += 1;
    }
  }

  return chars;
}

/**
 * Lay the collected characters into the pattern.
 *
 * A literal is only written when something follows it, so a half-typed
 * `00/00` reads `12` rather than `12/`. Literals before the first slot always
 * show, which is what a `+91 ` style prefix needs.
 */
function render(chars, tokens, opts) {
  let text = "";
  const literalAt = [];
  let charIndex = 0;
  let wroteSlot = false;

  for (const token of tokens) {
    if (token.kind === "literal") {
      if (charIndex < chars.length || opts.placeholder || !wroteSlot) {
        text += token.char;
        literalAt.push(true);
        continue;
      }
      break;
    }

    if (charIndex < chars.length) {
      text += chars[charIndex];
      charIndex += 1;
      literalAt.push(false);
      wroteSlot = true;
      continue;
    }

    if (opts.placeholder) {
      text += opts.placeholder;
      literalAt.push(false);
      wroteSlot = true;
      continue;
    }

    break;
  }

  return { text, literalAt };
}

/**
 * Format a value against a mask pattern.
 *
 * @param {string} value Raw or already-masked text.
 * @param {string} pattern
 * @param {{ placeholder?: string|null, uppercase?: boolean }} [options]
 *   `placeholder` fills unused slots (`"12/__"`); `uppercase` folds letters.
 * @returns {string}
 */
export function applyMask(value, pattern, options = {}) {
  const tokens = tokensFor(pattern);
  const opts = resolveOptions(options);
  return render(collect(String(value ?? ""), tokens, opts), tokens, opts).text;
}

/**
 * The characters that actually fill slots, with every separator removed.
 *
 * @param {string} value
 * @param {string} pattern
 * @returns {string}
 */
export function unmask(value, pattern) {
  const tokens = tokensFor(pattern);
  return collect(String(value ?? ""), tokens, resolveOptions({})).join("");
}

/**
 * True when every slot in the pattern is filled.
 *
 * @param {string} value
 * @param {string} pattern
 * @param {{ uppercase?: boolean }} [options]
 */
export function isComplete(value, pattern, options = {}) {
  const tokens = tokensFor(pattern);
  return collect(String(value ?? ""), tokens, resolveOptions(options)).length === slotCount(pattern);
}

/**
 * Where the caret should sit in the masked string.
 *
 * Take the caret position in the raw text the browser just produced, mask the
 * text before it, and the length of that prefix is the new caret — nudged past
 * any separator the pattern just inserted.
 *
 * @param {string} value The text currently in the field.
 * @param {number} caret Caret offset within `value`.
 * @param {string} pattern
 * @param {{ placeholder?: string|null, uppercase?: boolean }} [options]
 * @returns {number} Caret offset within the masked string.
 */
export function caretFor(value, caret, pattern, options = {}) {
  const tokens = tokensFor(pattern);
  const opts = resolveOptions(options);
  const text = String(value ?? "");
  const at = Math.min(Math.max(Number.isFinite(caret) ? caret : text.length, 0), text.length);

  const prefixChars = collect(text.slice(0, at), tokens, opts);
  const prefixLength = render(prefixChars, tokens, { ...opts, placeholder: null }).text.length;
  const { text: masked, literalAt } = render(collect(text, tokens, opts), tokens, opts);

  let index = prefixLength;
  while (index < masked.length && literalAt[index]) index += 1;
  return index;
}

/* ------------------------------------------------------------------ */
/* Binding to an <input>                                               */
/* ------------------------------------------------------------------ */

/**
 * Bind a mask to a text input: every edit is reformatted in place and the caret
 * is restored to where the user was typing.
 *
 * ```js
 * const field = new MaskInput(input, { preset: "aadhaar" });
 * field.value;    // "123456789012" — unmasked, ready to send
 * field.complete; // false until all 12 digits are in
 * ```
 */
export class MaskInput {
  /**
   * @param {HTMLInputElement} input
   * @param {{ pattern?: string, preset?: keyof typeof PRESETS,
   *   placeholder?: string|null, uppercase?: boolean,
   *   onChange?: (value: string, state: { masked: string, complete: boolean }) => void }} options
   */
  constructor(input, options = {}) {
    if (!input || typeof input.addEventListener !== "function") {
      throw new TypeError("mask-kit: MaskInput needs an <input> element");
    }

    const preset = options.preset ? PRESETS[options.preset] : null;
    if (options.preset && !preset) {
      throw new Error(`mask-kit: unknown preset "${options.preset}"`);
    }

    const pattern = options.pattern ?? preset?.pattern;
    if (!pattern) {
      throw new TypeError("mask-kit: pass a pattern or a preset");
    }

    this.input = input;
    this.pattern = pattern;
    this.options = {
      placeholder: options.placeholder ?? null,
      uppercase: options.uppercase ?? preset?.uppercase ?? false,
    };
    this.onChange = options.onChange ?? null;
    this._listener = () => this._sync();
    this._destroyed = false;

    input.addEventListener("input", this._listener);
    if (input.value) this.setValue(input.value);
  }

  /** Unmasked value: exactly what a form should submit. */
  get value() {
    return unmask(this.input.value, this.pattern);
  }

  /** Masked value, as displayed. */
  get masked() {
    return this.input.value;
  }

  /** True when every slot is filled. */
  get complete() {
    return isComplete(this.input.value, this.pattern, this.options);
  }

  /** Replace the field contents from raw text, reformatting it. */
  setValue(value) {
    const masked = applyMask(value, this.pattern, this.options);
    this.input.value = masked;
    this._emit();
    return masked;
  }

  /** Detach the listener. The field keeps its current text. */
  destroy() {
    if (this._destroyed) return;
    this.input.removeEventListener("input", this._listener);
    this._destroyed = true;
  }

  _sync() {
    const el = this.input;
    const raw = el.value;
    const caret = el.selectionStart ?? raw.length;
    const masked = applyMask(raw, this.pattern, this.options);
    const next = caretFor(raw, caret, this.pattern, this.options);

    el.value = masked;
    if (document.activeElement === el) {
      el.setSelectionRange(next, next);
    }
    this._emit();
  }

  _emit() {
    if (this.onChange) {
      this.onChange(this.value, { masked: this.input.value, complete: this.complete });
    }
  }
}
