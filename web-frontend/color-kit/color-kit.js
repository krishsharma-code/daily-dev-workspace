/**
 * color-kit.js — a small, dependency-free colour utility for the browser and
 * Node.
 *
 * Everything here is a pure function over plain objects: no DOM, no globals,
 * no build step. A colour is `{ r, g, b, a }` where `r`, `g`, `b` are integers
 * in `0..255` and `a` (alpha) is a number in `0..1`. HSL is `{ h, s, l }` with
 * `h` in `0..360` and `s`, `l` in `0..100`.
 *
 * The kit covers the three things you usually reach for a colour library to do:
 *
 *   1. Parse and format colours across hex, `rgb()`/`rgba()` and
 *      `hsl()`/`hsla()` notations.
 *   2. Convert between RGB and HSL, and derive new colours (lighten, darken,
 *      mix, rotate hue, complement, composite alpha).
 *   3. Check contrast — relative luminance, the WCAG contrast ratio, and the
 *      AA/AAA pass levels — so you can pick accessible text colours.
 *
 * Parse functions throw a `TypeError` on input they cannot understand, rather
 * than returning a half-valid colour.
 */

/** Clamp `n` into the inclusive range `[lo, hi]`. */
function clamp(n, lo, hi) {
  return n < lo ? lo : n > hi ? hi : n;
}

/** Round to the nearest integer, avoiding `-0`. */
function round(n) {
  return Math.round(n) + 0 || 0;
}

/** Clamp an alpha channel into `0..1`; treat anything non-finite as opaque. */
function clampAlpha(a) {
  const n = Number(a);
  return Number.isFinite(n) ? clamp(n, 0, 1) : 1;
}

/** Build a colour object, normalising and clamping the channels. */
export function rgb(r, g, b, a = 1) {
  return { r: clamp(round(r), 0, 255), g: clamp(round(g), 0, 255), b: clamp(round(b), 0, 255), a: clampAlpha(a) };
}

/** Reject values that are not usable numbers, returning the value when valid. */
function assertNumber(value, what) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(`${what} must be a finite number, got ${JSON.stringify(value)}`);
  }
  return value;
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

/**
 * Parse a hex colour: `#rgb`, `#rgba`, `#rrggbb` or `#rrggbbaa`, with or
 * without the leading `#`. Returns `{ r, g, b, a }`.
 */
export function parseHex(input) {
  if (typeof input !== "string") throw new TypeError("hex colour must be a string");
  const hex = input.trim().replace(/^#/, "");
  if (!/^[0-9a-fA-F]+$/.test(hex) || ![3, 4, 6, 8].includes(hex.length)) {
    throw new TypeError(`invalid hex colour: ${JSON.stringify(input)}`);
  }
  let parts;
  if (hex.length <= 4) {
    parts = [...hex].map((c) => parseInt(c + c, 16));
  } else {
    parts = hex.match(/[0-9a-fA-F]{2}/g).map((c) => parseInt(c, 16));
  }
  const [r, g, b, a] = parts;
  return rgb(r, g, b, a === undefined ? 1 : a / 255);
}

/**
 * Parse an `rgb()` / `rgba()` string. Accepts comma or space separated
 * channels, percentages, and a slash-separated alpha:
 * `rgb(255, 0, 0)`, `rgba(255 0 0 / 0.5)`, `rgb(100%, 0%, 0%)`.
 */
export function parseRgb(input) {
  if (typeof input !== "string") throw new TypeError("rgb colour must be a string");
  const m = input.trim().match(/^rgba?\(([^)]+)\)$/i);
  if (!m) throw new TypeError(`invalid rgb colour: ${JSON.stringify(input)}`);
  const { channels, alpha } = splitChannels(m[1], "rgb");

  const channel = (token) => {
    const isPercent = token.endsWith("%");
    const value = Number.parseFloat(token);
    if (!Number.isFinite(value)) throw new TypeError(`invalid rgb channel: ${JSON.stringify(token)}`);
    return isPercent ? (value / 100) * 255 : value;
  };

  return rgb(channel(channels[0]), channel(channels[1]), channel(channels[2]), alpha);
}

/**
 * Split the inside of a colour function into three colour channels plus an
 * optional alpha, accepting both the comma form (`r, g, b, a`) and the modern
 * space/slash form (`r g b / a`).
 */
function splitChannels(body, name) {
  let channelPart = body;
  let alphaPart;
  if (body.includes("/")) {
    [channelPart, alphaPart] = body.split("/");
  }
  let tokens = channelPart.trim().split(/[\s,]+/).filter(Boolean);
  if (tokens.length === 4 && alphaPart === undefined) {
    alphaPart = tokens[3];
    tokens = tokens.slice(0, 3);
  }
  if (tokens.length !== 3) throw new TypeError(`${name}() needs 3 channels: ${JSON.stringify(body)}`);
  if (alphaPart === undefined || alphaPart.trim() === "") return { channels: tokens, alpha: 1 };
  const value = Number.parseFloat(alphaPart);
  if (!Number.isFinite(value)) throw new TypeError(`invalid alpha: ${JSON.stringify(alphaPart)}`);
  return { channels: tokens, alpha: value };
}

/**
 * Parse an `hsl()` / `hsla()` string into an `{ r, g, b, a }` colour. Accepts
 * `hsl(120, 50%, 50%)` and the modern `hsl(120 50% 50% / 0.5)` form. The hue
 * may carry a `deg` suffix; saturation and lightness must be percentages.
 */
export function parseHsl(input) {
  if (typeof input !== "string") throw new TypeError("hsl colour must be a string");
  const m = input.trim().match(/^hsla?\(([^)]+)\)$/i);
  if (!m) throw new TypeError(`invalid hsl colour: ${JSON.stringify(input)}`);
  const { channels, alpha } = splitChannels(m[1], "hsl");

  const h = Number.parseFloat(channels[0].replace(/deg$/i, ""));
  const s = Number.parseFloat(channels[1]);
  const l = Number.parseFloat(channels[2]);
  if (![h, s, l].every(Number.isFinite)) {
    throw new TypeError(`invalid hsl channels: ${JSON.stringify(input)}`);
  }
  if (!channels[1].endsWith("%") || !channels[2].endsWith("%")) {
    throw new TypeError(`hsl() saturation and lightness must be percentages: ${JSON.stringify(input)}`);
  }
  return { ...hslToRgb({ h, s, l }), a: clampAlpha(alpha) };
}

/**
 * Parse any supported colour notation (`#hex`, `rgb()`, `hsl()`), or accept an
 * already-parsed `{ r, g, b }` object and return a normalised colour.
 */
export function parseColor(input) {
  if (input && typeof input === "object") return rgb(input.r, input.g, input.b, input.a ?? 1);
  if (typeof input !== "string") throw new TypeError("colour must be a string or an object");
  const value = input.trim();
  if (/^#?[0-9a-fA-F]{3,8}$/.test(value) && !/^(rgb|hsl)/i.test(value)) return parseHex(value);
  if (/^rgba?\(/i.test(value)) return parseRgb(value);
  if (/^hsla?\(/i.test(value)) return parseHsl(value);
  throw new TypeError(`unrecognised colour: ${JSON.stringify(input)}`);
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/** Format as `#rrggbb`, or `#rrggbbaa` when the colour is not fully opaque. */
export function toHex(color) {
  const c = parseColor(color);
  const pair = (n) => clamp(round(n), 0, 255).toString(16).padStart(2, "0");
  const base = `#${pair(c.r)}${pair(c.g)}${pair(c.b)}`;
  return c.a < 1 ? `${base}${pair(c.a * 255)}` : base;
}

/** Format as `rgb(r, g, b)` or `rgba(r, g, b, a)`. */
export function toRgbString(color) {
  const c = parseColor(color);
  return c.a < 1
    ? `rgba(${c.r}, ${c.g}, ${c.b}, ${+c.a.toFixed(3)})`
    : `rgb(${c.r}, ${c.g}, ${c.b})`;
}

/** Format as `hsl(h, s%, l%)` or `hsla(h, s%, l%, a)`. */
export function toHslString(color) {
  const c = parseColor(color);
  const { h, s, l } = rgbToHsl(c);
  const hh = round(h);
  const ss = round(s);
  const ll = round(l);
  return c.a < 1
    ? `hsla(${hh}, ${ss}%, ${ll}%, ${+c.a.toFixed(3)})`
    : `hsl(${hh}, ${ss}%, ${ll}%)`;
}

// ---------------------------------------------------------------------------
// Conversion
// ---------------------------------------------------------------------------

/** Convert `{ r, g, b }` to `{ h, s, l }` (hue in degrees, s/l as percentages). */
export function rgbToHsl(color) {
  const c = parseColor(color);
  const r = c.r / 255;
  const g = c.g / 255;
  const b = c.b / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const delta = max - min;

  if (delta === 0) return { h: 0, s: 0, l: l * 100 };

  const s = l > 0.5 ? delta / (2 - max - min) : delta / (max + min);
  let h;
  if (max === r) h = ((g - b) / delta) % 6;
  else if (max === g) h = (b - r) / delta + 2;
  else h = (r - g) / delta + 4;

  h *= 60;
  if (h < 0) h += 360;
  return { h, s: s * 100, l: l * 100 };
}

/** Convert `{ h, s, l }` to `{ r, g, b }`. Hue wraps; saturation/lightness clamp. */
export function hslToRgb(color) {
  if (!color || typeof color !== "object") throw new TypeError("hsl must be an object");
  const h = (((assertNumber(color.h, "hue") % 360) + 360) % 360) / 360;
  const s = clamp(assertNumber(color.s, "saturation"), 0, 100) / 100;
  const l = clamp(assertNumber(color.l, "lightness"), 0, 100) / 100;

  if (s === 0) {
    const v = l * 255;
    return rgb(v, v, v);
  }

  const hue2rgb = (p, q, t) => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };

  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  return rgb(hue2rgb(p, q, h + 1 / 3) * 255, hue2rgb(p, q, h) * 255, hue2rgb(p, q, h - 1 / 3) * 255);
}

// ---------------------------------------------------------------------------
// Deriving colours
// ---------------------------------------------------------------------------

/** Return a new colour with the given alpha (`0..1`). */
export function withAlpha(color, alpha) {
  const c = parseColor(color);
  return { ...c, a: clampAlpha(alpha) };
}

/**
 * Lighten by adding `amount` percentage points of lightness (default 10).
 * `darken` is the inverse. Both keep hue and saturation.
 */
export function lighten(color, amount = 10) {
  const c = parseColor(color);
  const { h, s, l } = rgbToHsl(c);
  return { ...hslToRgb({ h, s, l: clamp(l + amount, 0, 100) }), a: c.a };
}

/** Darken by subtracting `amount` percentage points of lightness (default 10). */
export function darken(color, amount = 10) {
  return lighten(color, -amount);
}

/** Rotate the hue by `degrees` (may be negative); hue wraps around 360. */
export function rotateHue(color, degrees) {
  const c = parseColor(color);
  const { h, s, l } = rgbToHsl(c);
  return { ...hslToRgb({ h: h + degrees, s, l }), a: c.a };
}

/** The complement: hue rotated 180 degrees. */
export function complement(color) {
  return rotateHue(color, 180);
}

/**
 * Mix two colours in linear RGB. `weight` is how much of `c2` to use, from `0`
 * (all `c1`) to `1` (all `c2`); the default `0.5` is an even blend. Alpha is
 * blended by the same weight.
 */
export function mix(c1, c2, weight = 0.5) {
  assertNumber(weight, "weight");
  const w = clamp(weight, 0, 1);
  const a = parseColor(c1);
  const b = parseColor(c2);
  return rgb(
    a.r + (b.r - a.r) * w,
    a.g + (b.g - a.g) * w,
    a.b + (b.b - a.b) * w,
    a.a + (b.a - a.a) * w,
  );
}

/**
 * Composite `fg` over an opaque `bg` using source-over alpha blending, and
 * return the resulting opaque colour. Useful for previewing translucent colours.
 */
export function blendOver(fg, bg) {
  const f = parseColor(fg);
  const b = parseColor(bg);
  const a = f.a;
  return rgb(
    f.r * a + b.r * (1 - a),
    f.g * a + b.g * (1 - a),
    f.b * a + b.b * (1 - a),
    1,
  );
}

// ---------------------------------------------------------------------------
// Contrast & accessibility
// ---------------------------------------------------------------------------

/** WCAG relative luminance of a colour, in `0..1`. Alpha is ignored. */
export function relativeLuminance(color) {
  const c = parseColor(color);
  const channel = (v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
}

/**
 * WCAG 2.1 contrast ratio between two colours, from `1` (identical) to `21`
 * (black on white). Order of arguments does not matter.
 */
export function contrastRatio(c1, c2) {
  const l1 = relativeLuminance(c1);
  const l2 = relativeLuminance(c2);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * WCAG 2.1 conformance for a colour pair. Pass `large: true` for text of at
 * least 18pt (or 14pt bold). Returns the ratio plus `aa` and `aaa` booleans.
 */
export function wcag(c1, c2, { large = false } = {}) {
  const ratio = contrastRatio(c1, c2);
  const aaMin = large ? 3 : 4.5;
  const aaaMin = large ? 4.5 : 7;
  return { ratio, aa: ratio >= aaMin, aaa: ratio >= aaaMin };
}

/**
 * Pick the more readable of black and white for text on `background`, judged by
 * contrast ratio. Returns `"#000000"` or `"#ffffff"`.
 */
export function readableTextColor(background) {
  const black = contrastRatio(background, "#000000");
  const white = contrastRatio(background, "#ffffff");
  return black >= white ? "#000000" : "#ffffff";
}
