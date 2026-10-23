import { test } from "node:test";
import assert from "node:assert/strict";

import {
  rgb,
  parseHex,
  parseRgb,
  parseHsl,
  parseColor,
  toHex,
  toRgbString,
  toHslString,
  rgbToHsl,
  hslToRgb,
  withAlpha,
  lighten,
  darken,
  rotateHue,
  complement,
  mix,
  blendOver,
  relativeLuminance,
  contrastRatio,
  wcag,
  readableTextColor,
} from "./color-kit.js";

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

test("parseHex reads 3-, 4-, 6- and 8-digit hex", () => {
  assert.deepEqual(parseHex("#f00"), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(parseHex("f00"), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(parseHex("#ff0000"), { r: 255, g: 0, b: 0, a: 1 });
  assert.equal(parseHex("#0f08").a, 136 / 255);
  assert.equal(parseHex("#ff000080").a, 128 / 255);
  assert.equal(parseHex("#ff0000ff").a, 1);
});

test("parseHex is case-insensitive and trims", () => {
  assert.deepEqual(parseHex("  #FF8000 "), { r: 255, g: 128, b: 0, a: 1 });
});

test("parseHex rejects malformed input", () => {
  for (const bad of ["#12", "#12345", "gggggg", "#ff00zz", "", 42]) {
    assert.throws(() => parseHex(bad), TypeError);
  }
});

test("parseRgb handles comma, space, percentage and slash forms", () => {
  assert.deepEqual(parseRgb("rgb(255, 0, 0)"), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(parseRgb("rgb(255 0 0)"), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(parseRgb("rgba(255, 0, 0, 0.5)"), { r: 255, g: 0, b: 0, a: 0.5 });
  assert.deepEqual(parseRgb("rgba(255 0 0 / 0.5)"), { r: 255, g: 0, b: 0, a: 0.5 });
  assert.deepEqual(parseRgb("rgb(100%, 0%, 0%)"), { r: 255, g: 0, b: 0, a: 1 });
});

test("parseRgb rejects wrong channel counts and bad values", () => {
  for (const bad of ["rgb(1, 2)", "rgb(1, 2, 3, 4, 5)", "rgb(a, b, c)", "nope"]) {
    assert.throws(() => parseRgb(bad), TypeError);
  }
});

test("parseHsl converts to rgb and requires percentage s/l", () => {
  assert.deepEqual(parseHsl("hsl(120, 50%, 50%)"), { r: 64, g: 191, b: 64, a: 1 });
  assert.deepEqual(parseHsl("hsl(120 50% 50% / 0.25)"), { r: 64, g: 191, b: 64, a: 0.25 });
  assert.deepEqual(parseHsl("hsla(0, 100%, 50%, 0.5)"), { r: 255, g: 0, b: 0, a: 0.5 });
  assert.throws(() => parseHsl("hsl(120, 50, 50)"), TypeError);
});

test("parseColor dispatches on notation and accepts objects", () => {
  assert.deepEqual(parseColor("#0f0"), { r: 0, g: 255, b: 0, a: 1 });
  assert.deepEqual(parseColor("rgb(0, 255, 0)"), { r: 0, g: 255, b: 0, a: 1 });
  assert.deepEqual(parseColor("hsl(120, 100%, 50%)"), { r: 0, g: 255, b: 0, a: 1 });
  assert.deepEqual(parseColor({ r: 0, g: 255, b: 0 }), { r: 0, g: 255, b: 0, a: 1 });
  assert.deepEqual(parseColor({ r: 0, g: 255, b: 0, a: 0.5 }).a, 0.5);
  assert.throws(() => parseColor("chartreuse"), TypeError);
  assert.throws(() => parseColor(null), TypeError);
});

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

test("toHex emits #rrggbb, adding alpha only when needed", () => {
  assert.equal(toHex("#f00"), "#ff0000");
  assert.equal(toHex({ r: 0, g: 255, b: 0 }), "#00ff00");
  assert.equal(toHex("#ff000080"), "#ff000080");
});

test("toRgbString and toHslString round-trip", () => {
  assert.equal(toRgbString("#ff0000"), "rgb(255, 0, 0)");
  assert.equal(toRgbString("rgba(255, 0, 0, 0.5)"), "rgba(255, 0, 0, 0.5)");
  assert.equal(toHslString("#ff0000"), "hsl(0, 100%, 50%)");
  assert.equal(toHslString("#40bf40"), "hsl(120, 50%, 50%)");
  assert.equal(toHslString("hsla(0, 100%, 50%, 0.5)"), "hsla(0, 100%, 50%, 0.5)");
});

test("formatting then parsing returns the original colour", () => {
  for (const value of ["#123456", "#abcdef", "#010203"]) {
    assert.equal(toHex(parseColor(value)), value);
  }
});

// ---------------------------------------------------------------------------
// Conversion
// ---------------------------------------------------------------------------

test("rgbToHsl handles primaries, greys and black", () => {
  assert.deepEqual(rgbToHsl("#ff0000"), { h: 0, s: 100, l: 50 });
  assert.deepEqual(rgbToHsl("#00ff00"), { h: 120, s: 100, l: 50 });
  assert.deepEqual(rgbToHsl("#0000ff"), { h: 240, s: 100, l: 50 });
  assert.deepEqual(rgbToHsl("#ffffff"), { h: 0, s: 0, l: 100 });
  assert.deepEqual(rgbToHsl("#000000"), { h: 0, s: 0, l: 0 });
});

test("hslToRgb inverts rgbToHsl", () => {
  for (const hex of ["#ff0000", "#00ff00", "#0000ff", "#40bf40", "#7a5c2e"]) {
    const hsl = rgbToHsl(hex);
    assert.equal(toHex(hslToRgb(hsl)), hex);
  }
});

test("hslToRgb wraps hue and clamps saturation/lightness", () => {
  assert.deepEqual(hslToRgb({ h: 480, s: 100, l: 50 }), hslToRgb({ h: 120, s: 100, l: 50 }));
  assert.deepEqual(hslToRgb({ h: -120, s: 100, l: 50 }), hslToRgb({ h: 240, s: 100, l: 50 }));
  assert.deepEqual(hslToRgb({ h: 0, s: 500, l: 500 }), { r: 255, g: 255, b: 255, a: 1 });
});

test("rgb() clamps channels and alpha", () => {
  assert.deepEqual(rgb(300, -10, 20), { r: 255, g: 0, b: 20, a: 1 });
  assert.equal(rgb(0, 0, 0, 2).a, 1);
  assert.equal(rgb(0, 0, 0, -1).a, 0);
  assert.equal(rgb(0, 0, 0, NaN).a, 1);
});

// ---------------------------------------------------------------------------
// Deriving colours
// ---------------------------------------------------------------------------

test("withAlpha replaces alpha and keeps channels", () => {
  assert.deepEqual(withAlpha("#ff0000", 0.25), { r: 255, g: 0, b: 0, a: 0.25 });
});

test("lighten and darken move lightness without changing hue", () => {
  assert.equal(toHex(lighten("#000000", 10)), "#1a1a1a");
  assert.equal(toHex(darken("#ffffff", 10)), "#e6e6e6");
  const red = rgbToHsl("#cc0000");
  assert.equal(rgbToHsl(lighten("#cc0000", 15)).h, red.h);
  // lighten clamps at white, darken at black
  assert.equal(toHex(lighten("#ffffff", 50)), "#ffffff");
  assert.equal(toHex(darken("#000000", 50)), "#000000");
});

test("rotateHue spins the wheel and complement is 180 degrees", () => {
  assert.equal(toHex(rotateHue("#ff0000", 120)), "#00ff00");
  assert.equal(toHex(rotateHue("#ff0000", -120)), "#0000ff");
  assert.equal(toHex(complement("#ff0000")), "#00ffff");
  // a full turn is a no-op
  assert.equal(toHex(rotateHue("#ff0000", 360)), "#ff0000");
});

test("mix blends by weight and is exact at the endpoints", () => {
  assert.equal(toHex(mix("#000000", "#ffffff", 0.5)), "#808080");
  assert.equal(toHex(mix("#000000", "#ffffff", 0)), "#000000");
  assert.equal(toHex(mix("#000000", "#ffffff", 1)), "#ffffff");
  assert.equal(toHex(mix("#ff0000", "#0000ff", 0.5)), "#800080");
  assert.equal(mix("#ff0000", "#0000ff", 5).b, 255);
});

test("blendOver composites a translucent colour over an opaque one", () => {
  assert.equal(toHex(blendOver("rgba(255, 0, 0, 0.5)", "#ffffff")), "#ff8080");
  assert.equal(toHex(blendOver("rgba(0, 0, 0, 0)", "#123456")), "#123456");
  assert.equal(toHex(blendOver("#ff0000", "#0000ff")), "#ff0000");
});

// ---------------------------------------------------------------------------
// Contrast & accessibility
// ---------------------------------------------------------------------------

test("relativeLuminance matches the WCAG endpoints", () => {
  assert.equal(relativeLuminance("#000000"), 0);
  assert.equal(relativeLuminance("#ffffff"), 1);
  assert.equal(+relativeLuminance("#00ff00").toFixed(4), 0.7152);
});

test("contrastRatio spans 1..21 and is symmetric", () => {
  assert.equal(contrastRatio("#ffffff", "#000000"), 21);
  assert.equal(contrastRatio("#ffffff", "#ffffff"), 1);
  assert.equal(
    contrastRatio("#123456", "#abcdef"),
    contrastRatio("#abcdef", "#123456"),
  );
});

test("wcag reports the ratio and the AA/AAA levels", () => {
  assert.deepEqual(wcag("#000000", "#ffffff"), { ratio: 21, aa: true, aaa: true });
  // #777777 on white is just under the 4.5 normal-text bar
  const normal = wcag("#777777", "#ffffff");
  assert.equal(normal.aa, false);
  assert.equal(normal.aaa, false);
  // the same pair passes AA for large text
  const large = wcag("#777777", "#ffffff", { large: true });
  assert.equal(large.aa, true);
});

test("readableTextColor picks the higher-contrast ink", () => {
  assert.equal(readableTextColor("#ffffff"), "#000000");
  assert.equal(readableTextColor("#000000"), "#ffffff");
  assert.equal(readableTextColor("#00ff00"), "#000000");
  assert.equal(readableTextColor("#001a33"), "#ffffff");
});

// ---------------------------------------------------------------------------
// More edge cases
// ---------------------------------------------------------------------------

test("8-digit hex survives a parse/format round-trip", () => {
  assert.equal(toHex(parseColor("#ff000080")), "#ff000080");
  assert.equal(toHex(withAlpha("#000000", 0.5)), "#00000080");
});

test("parseRgb clamps out-of-range channels and reads percentages", () => {
  assert.deepEqual(parseRgb("rgb(300, -5, 0)"), { r: 255, g: 0, b: 0, a: 1 });
  assert.deepEqual(parseRgb("rgb(50%, 50%, 50%)"), { r: 128, g: 128, b: 128, a: 1 });
});

test("parseHsl clamps out-of-range alpha and saturation", () => {
  assert.equal(parseHsl("hsl(120, 50%, 50% / 4)").a, 1);
  assert.deepEqual(parseHsl("hsl(0, 200%, 50%)"), parseHsl("hsl(0, 100%, 50%)"));
});

test("hslToRgb rejects non-numeric channels", () => {
  assert.throws(() => hslToRgb({ h: "x", s: 50, l: 50 }), TypeError);
  assert.throws(() => hslToRgb({ s: 50, l: 50 }), TypeError);
  assert.throws(() => hslToRgb(null), TypeError);
});

test("withAlpha clamps to 0..1", () => {
  assert.equal(withAlpha("#ff0000", 5).a, 1);
  assert.equal(withAlpha("#ff0000", -1).a, 0);
});

test("mix validates its weight argument", () => {
  assert.throws(() => mix("#000000", "#ffffff", "half"), TypeError);
});

test("derived colours keep the source alpha", () => {
  assert.equal(rotateHue("rgba(255, 0, 0, 0.4)", 90).a, 0.4);
  assert.equal(lighten("rgba(0, 0, 0, 0.25)", 10).a, 0.25);
  assert.equal(mix("rgba(0, 0, 0, 0)", "rgba(255, 255, 255, 1)", 0.5).a, 0.5);
});

test("blendOver always yields an opaque colour", () => {
  assert.equal(blendOver("rgba(255, 0, 0, 0.3)", "rgba(0, 0, 255, 1)").a, 1);
});

test("complement of a grey is itself", () => {
  assert.equal(toHex(complement("#808080")), "#808080");
});

test("relativeLuminance sits between the extremes for mid tones", () => {
  const mid = relativeLuminance("#808080");
  assert.ok(mid > 0 && mid < 1);
  assert.ok(relativeLuminance("#eeeeee") > mid);
  assert.ok(relativeLuminance("#111111") < mid);
});

test("wcag: a 4.5..7 ratio passes AA but not AAA", () => {
  const result = wcag("#767676", "#ffffff");
  assert.ok(result.ratio > 4.5 && result.ratio < 7);
  assert.equal(result.aa, true);
  assert.equal(result.aaa, false);
});

test("readableTextColor stays readable on mid greys", () => {
  assert.equal(readableTextColor("#808080"), "#000000");
  const ink = readableTextColor("#808080");
  assert.ok(contrastRatio("#808080", ink) >= contrastRatio("#808080", "#ffffff"));
});
