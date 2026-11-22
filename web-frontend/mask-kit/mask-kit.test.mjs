import test from "node:test";
import assert from "node:assert/strict";

import {
  PRESETS,
  parsePattern,
  slotCount,
  applyMask,
  unmask,
  isComplete,
  caretFor,
  MaskInput,
} from "./mask-kit.js";

test("parsePattern splits slots and literals", () => {
  assert.deepEqual(parsePattern("00/00"), [
    { kind: "digit" },
    { kind: "digit" },
    { kind: "literal", char: "/" },
    { kind: "digit" },
    { kind: "digit" },
  ]);
});

test("parsePattern understands escapes", () => {
  assert.deepEqual(parsePattern("\\0A*"), [
    { kind: "literal", char: "0" },
    { kind: "letter" },
    { kind: "alnum" },
  ]);
  assert.deepEqual(parsePattern("\\\\"), [{ kind: "literal", char: "\\" }]);
});

test("parsePattern rejects an empty pattern and a dangling escape", () => {
  assert.throws(() => parsePattern(""), TypeError);
  assert.throws(() => parsePattern("00\\"), /dangling escape/);
});

test("slotCount counts only fillable positions", () => {
  assert.equal(slotCount("0000 0000 0000"), 12);
  assert.equal(slotCount("AA 00 AA 0000"), 10);
  assert.equal(slotCount("abc"), 0);
});

test("applyMask inserts separators as the digits arrive", () => {
  assert.equal(applyMask("9876543210", "00000 00000"), "98765 43210");
  assert.equal(applyMask("4111111111111111", "0000 0000 0000 0000"), "4111 1111 1111 1111");
});

test("applyMask does not leave a trailing separator behind", () => {
  assert.equal(applyMask("", "00/00"), "");
  assert.equal(applyMask("1", "00/00"), "1");
  assert.equal(applyMask("12", "00/00"), "12");
  assert.equal(applyMask("123", "00/00"), "12/3");
});

test("applyMask drops characters that cannot fill the next slot", () => {
  assert.equal(applyMask("12ab34", "00/00"), "12/34");
  assert.equal(applyMask("abc98765", "00000"), "98765");
});

test("applyMask ignores separators already present in the input", () => {
  assert.equal(applyMask("12/34", "00/00"), "12/34");
  assert.equal(applyMask("12-34", "00/00"), "12/34");
  assert.equal(applyMask("98765-43210", "00000 00000"), "98765 43210");
});

test("applyMask shows literals that sit before the first slot", () => {
  assert.equal(applyMask("", "+91 00000 00000"), "+91 ");
  assert.equal(applyMask("+919876543210", "+91 00000 00000"), "+91 98765 43210");
  assert.equal(applyMask("", "(000) 000-0000"), "(");
});

test("applyMask honours the placeholder option", () => {
  assert.equal(applyMask("12", "00/00/0000", { placeholder: "_" }), "12/__/____");
  assert.equal(applyMask("", "000000", { placeholder: "•" }), "••••••");
  assert.equal(applyMask("123456", "00/00", { placeholder: "_" }), "12/34");
});

test("applyMask folds letters when uppercase is set", () => {
  assert.equal(applyMask("abcde1234f", "AAAAA0000A", { uppercase: true }), "ABCDE1234F");
  assert.equal(applyMask("abcde1234f", "AAAAA0000A"), "abcde1234f");
});

test("applyMask stops once every slot is full", () => {
  assert.equal(applyMask("123456789", "000000"), "123456");
});

test("unmask returns only the characters that fill slots", () => {
  assert.equal(unmask("4111 1111 1111 1111", "0000 0000 0000 0000"), "4111111111111111");
  assert.equal(unmask("12/34", "00/00"), "1234");
  assert.equal(unmask("(987) 654-3210", "(000) 000-0000"), "9876543210");
});

test("a pasted GSTIN keeps its entity code, Z and checksum", () => {
  // The 12th character is always Z; keeping it a slot means unmask() returns
  // the full 14-character identifier rather than a 13-character prefix.
  const gstin = "27AAPFU0939F1ZV";
  assert.equal(applyMask(gstin, PRESETS.gstin.pattern, PRESETS.gstin), gstin);
  assert.equal(unmask(gstin, PRESETS.gstin.pattern), gstin);
  assert.equal(slotCount(PRESETS.gstin.pattern), 15); // GSTIN is 15 characters
});

test("isComplete reports whether every slot is filled", () => {
  assert.equal(isComplete("12/34", "00/00"), true);
  assert.equal(isComplete("12/3", "00/00"), false);
  assert.equal(isComplete("", "00/00"), false);
  assert.equal(isComplete("12/34", "00/00", { uppercase: true }), true);
});

test("caretFor keeps the caret at the end while typing", () => {
  assert.equal(caretFor("1", 1, "00/00"), 1);
  assert.equal(caretFor("12", 2, "00/00"), 2);
  assert.equal(caretFor("123", 3, "00/00"), 4);
  assert.equal(caretFor("12/3", 4, "00/00"), 4);
  assert.equal(caretFor("1234", 4, "00/00"), 5);
});

test("caretFor steps over a separator the pattern just inserted", () => {
  assert.equal(caretFor("1234", 2, "00/00"), 3);
  assert.equal(caretFor("9876543210", 5, "00000 00000"), 6);
});

test("caretFor clamps an out-of-range caret", () => {
  assert.equal(caretFor("12", 99, "00/00"), 2);
  assert.equal(caretFor("12", -5, "00/00"), 0);
});

test("caretFor lands after the last character when the mask is full", () => {
  assert.equal(caretFor("4111111111111111", 16, "0000 0000 0000 0000"), 19);
});

test("every preset parses and round-trips a filled value", () => {
  const sample = { digit: "7", letter: "x", alnum: "9" };

  for (const [name, preset] of Object.entries(PRESETS)) {
    const tokens = parsePattern(preset.pattern);
    const raw = tokens
      .filter((token) => token.kind !== "literal")
      .map((token) => sample[token.kind])
      .join("");
    const options = { uppercase: preset.uppercase };

    const masked = applyMask(raw, preset.pattern, options);
    assert.equal(unmask(masked, preset.pattern).length, slotCount(preset.pattern), name);
    assert.equal(isComplete(masked, preset.pattern, options), true, name);
    assert.equal(applyMask(masked, preset.pattern, options), masked, `${name} is idempotent`);
  }
});

test("MaskInput rejects anything that is not an input", () => {
  assert.throws(() => new MaskInput(null, { preset: "pan" }), TypeError);
});

test("MaskInput rejects an unknown preset and a missing pattern", () => {
  const fake = { addEventListener() {}, removeEventListener() {} };
  assert.throws(() => new MaskInput(fake, { preset: "nope" }), /unknown preset/);
  assert.throws(() => new MaskInput(fake), TypeError);
});

test("MaskInput exposes masked, unmasked and complete", () => {
  const fake = { value: "", addEventListener() {}, removeEventListener() {} };
  const field = new MaskInput(fake, { preset: "aadhaar" });

  field.setValue("123456789012");
  assert.equal(field.masked, "1234 5678 9012");
  assert.equal(field.value, "123456789012");
  assert.equal(field.complete, true);

  field.setValue("1234");
  assert.equal(field.masked, "1234");
  assert.equal(field.value, "1234");
  assert.equal(field.complete, false);
});
