# mask-kit

Input masks from a tiny pattern language: the separators are inserted for you,
characters that do not fit are dropped, and the caret stays where you were
typing. Dependency-free, an ES module, and it runs unchanged in the browser and
in Node.

Most mask libraries get the boring parts wrong — a half-typed `12` renders as
`12/`, a paste of an already-formatted value shifts every character by one, and
the caret jumps to the end of the field after each keystroke. mask-kit keeps a
mask to what a mask should be: a pattern, a set of slots, and a caret position.

```js
import { applyMask, unmask, isComplete, MaskInput } from "./mask-kit.js";

applyMask("9876543210", "00000 00000");          // "98765 43210"
applyMask("12", "00/00");                        // "12"      — no trailing "/"
applyMask("12ab34", "00/00");                    // "12/34"   — letters dropped
applyMask("12", "00/00/0000", { placeholder: "_" }); // "12/__/____"

unmask("12/34", "00/00");                        // "1234"
isComplete("12/34", "00/00");                    // true

const field = new MaskInput(document.querySelector("#aadhaar"), { preset: "aadhaar" });
field.value;    // "123456789012" — unmasked, ready to submit
field.complete; // true once all 12 digits are in
```

## The pattern language

| Token | Fills with |
| --- | --- |
| `0` | a digit `0-9` |
| `A` | a letter `A-Z` / `a-z` |
| `*` | a letter or a digit |
| anything else | a literal, inserted by the mask |
| `\0`, `\A`, `\*`, `\\` | that character, as a literal |

`"0000 0000 0000"` is Aadhaar, `"AA 00 AA 0000"` is a vehicle registration,
`"(000) 000-0000"` is a US phone number.

## Behaviour

- **Separators are inserted, never typed.** A literal is written only when
  something follows it, so `00/00` reads `12`, not `12/`. Literals before the
  first slot always show — that is what a `+91 ` prefix needs.
- **Invalid characters are dropped, not rejected.** Typing a letter into a
  digit slot does nothing; no `preventDefault`, no fighting the keyboard.
- **Formatted input is understood.** `12/34`, `12-34` and `1234` all fill
  `00/00`. A literal that appears in the pasted text is consumed as that
  literal, so a full GSTIN pastes in intact.
- **The caret is restored.** `caretFor(value, caret, pattern)` masks the text
  before the caret and returns the new offset, stepping over any separator the
  mask just inserted.
- **`unmask` is the submission value.** It returns only slot characters, so
  separators never leak into your payload.

Two limits worth knowing:

- A literal that is itself a digit or a letter can swallow input data — a
  pattern of `"+91 00000 00000"` eats the `9` of a bare `98765…` value. Prefer
  punctuation for literals, or paste the full `+91…` value.
- mask-kit formats; it does not validate. `12/99/2020` is a complete date mask
  and a nonsense date. Pair it with a validator for rules.

## API

| Function | Purpose |
| --- | --- |
| `applyMask(value, pattern, options)` | Format `value` against `pattern`. Options: `placeholder` (fills unused slots), `uppercase` (folds letters). |
| `unmask(value, pattern)` | The characters that fill slots, separators removed. |
| `isComplete(value, pattern, options)` | `true` when every slot is filled. |
| `caretFor(value, caret, pattern, options)` | Caret offset in the masked string for a caret at `caret` in `value`. |
| `parsePattern(pattern)` | The token list: `{ kind: "digit" \| "letter" \| "alnum" }` or `{ kind: "literal", char }`. |
| `slotCount(pattern)` | How many characters the mask holds. |
| `PRESETS` | Ready-made masks (below). |
| `MaskInput` | Binds a mask to an `<input>`: reformats on every edit, restores the caret, exposes `value`, `masked`, `complete`, `setValue()`, `destroy()`. |

`MaskInput` options: `pattern` or `preset`, plus `placeholder`, `uppercase` and
an `onChange(value, { masked, complete })` callback.

## Presets

| Preset | Pattern | Holds |
| --- | --- | --- |
| `aadhaar` | `0000 0000 0000` | 12 digits |
| `pan` | `AAAAA0000A` | 5 letters, 4 digits, 1 letter |
| `gstin` | `00AAAAA0000A***` | 15 characters, state code through checksum |
| `ifsc` | `AAAA0000000` | 4 letters, 7 alphanumerics |
| `mobile` | `00000 00000` | 10 digits (the `+91` belongs outside the field) |
| `pincode` | `000000` | 6 digits |
| `vehicle` | `AA 00 AA 0000` | state, RTO, series, number |
| `card` | `0000 0000 0000 0000` | 16 digits |
| `expiry` | `00/00` | MM/YY |
| `otp` | `000000` | 6 digits |
| `date` | `00/00/0000` | DD/MM/YYYY |

The presets format Indian identifiers; they do not check checksums. Use
[`python/india-id-kit`](../../python/india-id-kit/) for that side of the job.

## Run it

Open `index.html` in a browser — every preset and a custom-pattern playground,
no build step and no dependencies. Serving the folder
(`python3 -m http.server`) is only needed if your browser blocks module imports
over `file://`.

```sh
npm test        # node --test, 23 tests, no dependencies
```

## Files

```
mask-kit/
├── README.md
├── index.html         # live playground
├── mask-kit.css       # styles for the demo only
├── mask-kit.js        # the module
├── mask-kit.test.mjs  # node:test suite
└── package.json
```
